# AgentSession Model

Unified Redis model tracking agent work from enqueue through completion. Replaces both `AgentSession` (queue) and `AgentSession` (transcript) with a single `AgentSession` model in `models/agent_session.py`.

## Status Lifecycle

`pending` -> `running` -> `active` -> `dormant` -> `completed` | `failed` | `cancelled`

Additional non-terminal states: `waiting_for_children`, `superseded`, `paused_circuit`, `paused`, `paused_budget`.

The `cancelled` status is a terminal state set explicitly by the PM via `cancel_agent_session()`. Like `failed`, cancelled sessions block any sibling sessions that depend on them.

See [Session Lifecycle](session-lifecycle.md) for the full 14-state reference (9 non-terminal + 5 terminal).

## Key Fields

**Identity:** `id` (AutoKeyField), `session_id`, `session_type` (KeyField), `project_key` (KeyField), `chat_id` (KeyField), `status` (IndexedField). `agent_session_id` is a backward-compatible property alias for `id`.

**Queue-phase:** `priority`, `scheduled_at` (DatetimeField), `created_at` (SortedField, datetime), `started_at` (DatetimeField), `updated_at` (DatetimeField — UTC-stamped by `save()` override; see #1645), `completed_at` (DatetimeField), `auto_continue_count`

**Telegram origin (consolidated):** `initial_telegram_message` (DictField) — contains `sender_name`, `sender_id`, `message_text`, `telegram_message_id`, `chat_title`. Replaces the previous six separate fields. Property accessors (`sender_name`, `sender_id`, `message_text`) read from this dict for backward compatibility.

**Session-phase:** `turn_count`, `tool_call_count`, `log_path`, `branch_name`, `tags`, `context_summary`, `expectations`

**Extra context (consolidated):** `extra_context` (DictField) — contains `revival_context`, `classification_type`, `classification_confidence`, and other ad-hoc context. Property accessors expose individual fields.

**Lifecycle:** `session_events` (ListField of `SessionEvent` dicts), `issue_url`, `plan_url`, `pr_url`

**Resume:** `claude_session_uuid`, `resume_handles`. `resume_session()` (`tools/valor_session.py`) gates every `valor-session resume` on `claude_session_uuid` being non-null. The SDK-client path populates it via `_store_claude_session_uuid` (`agent/sdk_client.py`). Granite sessions populate it from the **PM** role handle in `BridgeAdapter._persist_resume_handles` (issue #1836): the PM handle's `claude_session_id` is mirrored onto `claude_session_uuid` so the gate passes. This is **PTY-PM only** (headless-PM is deferred to #1843) and is **rewritten with a fresh UUID on every run** — it reflects only the most recent run's PM transcript, not a durable resume anchor. `resume_handles` (the per-role list added by #1842) is the anchor #1721's cold→warm re-entry consumer reads; the scalar exists only to satisfy the gate.

**Parent-Child:** `parent_agent_session_id` (KeyField — canonical parent reference), `slug` (KeyField — derives branch, plan path, worktree; indexed so the slug-keyed worker-pop filter can find slugged eng sessions — see [Bridge/Worker Architecture §Three Worker Loop Archetypes](bridge-worker-architecture.md#three-worker-loop-archetypes)). The session role is carried by the `session_type` discriminator (`"eng"`/`"teammate"`/`"granite"`), not a separate `role` field.

**Watchdog:** `unhealthy_reason` — reason string set when the PostToolUse health check (consecutive-failure breaker or Haiku judge; see [Session Health Check](session-health-check.md)) flags a session unhealthy, `None` when healthy. Renamed from `watchdog_unhealthy` by the schema diet (#1927) below.

All timestamp fields use Popoto `DatetimeField` or `SortedField(type=datetime)` with proper UTC datetime objects. Float/int timestamps are auto-converted via `__setattr__`.

### Defensive coercion for `response_delivered_at`

`response_delivered_at` receives additional defensive coercion beyond the standard `int | float → datetime` conversion. This guards against Popoto's `is_valid()` coercion failure when sessions loaded from Redis (created before PR #923) have the field absent or holding a non-datetime value (e.g. the field descriptor object).

Coercion is applied in two places for defence-in-depth:

| Location | Coverage |
|---|---|
| `AgentSession.__setattr__` | All assignment paths: construction, Redis load, direct `session.field = value` |
| `AgentSession._normalize_kwargs` | Construction callsite: `AgentSession(...)`, `AgentSession.create(...)` |

Normalization rules for `response_delivered_at`:
- `int | float` → `datetime.fromtimestamp(value, tz=UTC)`
- `str` (valid ISO 8601) → `datetime.fromisoformat(value)`, normalized to UTC if naive
- `str` (unparseable) → `None` (logged at DEBUG level)
- any other non-`datetime`, non-`None` type → `None` (logged at DEBUG level)

**Why this matters:** Without coercion, a `DatetimeField` holding a non-datetime value causes `is_valid()` to return `False`, silently aborting `save()`. This causes `append_event("lifecycle", ...)` to drop the PM session status transition, leaving the session stuck at `status=running` in Redis and stalling the SDLC pipeline permanently (issue #929).

## SessionEvent (Structured Event Log)

`session_events` is a `ListField` of serialized `SessionEvent` Pydantic model dicts, replacing the old flat-string `history` field. Each event captures a lifecycle moment with typed fields.

`SessionEvent` (defined in `models/session_event.py`) has:
- `event_type` — `EventType` enum: `lifecycle`, `summary`, `delivery`, `stage`, `checkpoint`, `classify`, `system`, `user`
- `timestamp` — Unix float timestamp
- `text` — Human-readable description
- `data` — Optional structured payload dict

Factory methods: `SessionEvent.lifecycle()`, `.summary()`, `.delivery()`, `.stage_change()`, `.checkpoint()`, `.classify()`, `.system()`, `.user()`

`append_event(event_type, text, data)` appends events capped at 20 entries. When truncation occurs, a `WARNING`-level log is emitted.

### Derived Properties

Several fields that were previously stored as independent model fields are now derived from the event log:

| Property | Reads from | Write behavior |
|----------|-----------|----------------|
| `summary` | Last `summary` event text | Setter appends a `summary` event |
| `result_text` | Last `delivery` event text | Setter appends a `delivery` event |
| `stage_states` | Last `stage` event data | Setter appends a `stage` event |
| `last_commit_sha` | Last `checkpoint` event text | Setter appends a `checkpoint` event |
| `classification_type` | `extra_context["classification_type"]` | Setter updates `extra_context` |
| `scheduling_depth` | Walks `parent_agent_session_id` chain (max 5) | Read-only |

## SDLC Stage Tracking

Pipeline stage state is stored in `session_events` (as `stage` type events) on AgentSession, managed by the `PipelineStateMachine` in `agent/pipeline_state.py`. The `stage_states` property reads the latest stage event's data and returns the stages dict. The state machine provides:

| Method | Returns | Purpose |
|---|---|---|
| `PipelineStateMachine.has_remaining_stages()` | `bool` | `True` if pipeline graph has a non-terminal next stage from the last completed stage |
| `PipelineStateMachine.has_failed_stage()` | `bool` | `True` if any stage has `FAILED` or `ERROR` status |
| `PipelineStateMachine.get_display_progress()` | `dict` | Maps stage names to status — stored state only, no artifact inference |

`is_sdlc` (property) returns `True` if either (1) `stage_states` contains any non-pending/non-ready stage, or (2) `classification_type == ClassificationType.SDLC` for freshly-classified sessions.

String fields like `session_type` and `classification_type` use `StrEnum` members from `config/enums.py` (`SessionType`, `ClassificationType`). See [Standardized Enums](standardized-enums.md).

## Link Accumulation

`set_link(kind, url)` stores issue, plan, and PR URLs as each SDLC stage completes. `get_links()` returns all tracked links.

## Stage Tracking

Stage transitions are managed by the `PipelineStateMachine` in `agent/pipeline_state.py`. Stage status is set programmatically at transition points (`start_stage()`, `complete_stage()`, `fail_stage()`) rather than via a CLI tool.

| Skill | Stage | Transitions | Links Set |
|-------|-------|-------------|-----------|
| `/sdlc` | ISSUE | `completed` after issue verified | `issue-url` |
| `/do-plan` | PLAN | `in_progress` -> `completed` | `plan-url` |
| `/do-build` | BUILD | `in_progress` -> `completed` | `pr-url` |
| `/do-test` | TEST | `in_progress` -> `completed` or `failed` | — |
| `/do-pr-review` | REVIEW | `in_progress` -> `completed` | — |
| `/do-docs` | DOCS | `in_progress` -> `completed` | — |

### Raw-String Session Lookup

To look up an `AgentSession` from a raw string id (CLI arg, parent reference, Redis hash field), always use the canonical classmethod:

```python
session = AgentSession.get_by_id(agent_session_id)
```

**Why not `query.get(string)`?** Popoto's `query.get()` requires a key object (`db_key=` / `redis_key=` kwargs), not a positional string. Passing a bare string raises `AttributeError: 'str' object has no attribute 'redis_key'`. Historically these errors were swallowed by silent `except` blocks, causing lookups to silently return `None` even when the session existed (issue #765). The `get_by_id` helper handles `None`/empty/whitespace input gracefully and logs `WARNING`-level messages on backend failures — surfacing regressions instead of hiding them.

### Session Lookup Chain

`_find_session()` resolves an AgentSession using a three-tier lookup:

| Priority | Source | Description |
|----------|--------|-------------|
| 1 | `VALOR_SESSION_ID` env var | Bridge session_id, set by `session_executor.py`'s `_harness_env` |
| 2 | Direct `session_id` match | Works when caller has the bridge session_id |
| 3 | `task_list_id` match | Fallback for hook contexts with Claude Code UUID |

**Why three tiers?** Claude Code hooks receive Claude Code's internal UUID as `session_id`, which does not match the bridge's `AgentSession.session_id` (format: `tg_valor_{chat_id}_{msg_id}`). The hook session registry (see below) bridges this gap by giving hooks a direct path to the correct session. The `task_list_id` fallback provides belt-and-suspenders redundancy.

### VALOR_SESSION_ID Environment Variable

Set by `agent/session_executor.py`'s `_harness_env` alongside `CLAUDE_CODE_TASK_LIST_ID`. Propagated to all Claude Code subprocesses.

```python
# In session_executor.py _harness_env:
env["VALOR_SESSION_ID"] = session.session_id or ""
```

The key is **always** set in `_harness_env` — there is no conditional `if session_id:` guard. Its *value* is `session.session_id` when truthy, or an empty string when `session.session_id` is falsy. Local Claude Code sessions without bridge context will see this env var present but empty, and `_find_session()` falls back to the other lookup paths. For the full list of keys `_harness_env` sets, see [Harness Abstraction: env-contract table](harness-abstraction.md).

**Important**: This env var is available inside the Claude Code subprocess (for shell scripts, Python tools via Bash) but is **not** available to hooks. Hooks execute in the parent bridge process, not the subprocess. For hook-side session resolution, use the session registry (`agent/hooks/session_registry.py`) which maps Claude Code UUIDs to bridge session IDs via `resolve(claude_uuid)`. See [Session Isolation: Hook Session Registry](session-isolation.md#hook-session-registry-issue-597) for details.

### task_list_id Persistence

`task_list_id` is computed in `_execute_agent_session()` and persisted to the `AgentSession` immediately after the session is found:

- **Tier 1 (ad-hoc):** `thread-{chat_id}-{root_msg_id}`
- **Tier 2 (planned work):** The work item slug (e.g., `bridge-sdk-fix`)

This ensures hooks can resolve sessions via `task_list_id` even if `VALOR_SESSION_ID` is not available.

### Error Handling

- `_find_session()` catches Redis connection errors and returns `None`
- `main()` exits 0 when no session is found (fire-and-forget)
- Debug logging on `append_event()`, `set_link()`, `get_stage_progress()` via `logging.getLogger(__name__)`
- WARNING-level logging on `append_event()` and `set_link()` save failures, including operation context (role, field name)

## Session Lifecycle Integrity

### Single-Session Guarantee (Source of Truth Architecture)

Each `session_id` has exactly one `AgentSession` at any time. The `AgentSession` is the single source of truth for all session metadata -- no state needs to be passed as parameters between functions.

**Session creation:** `_push_agent_session()` creates the session at enqueue time. `start_transcript()` updates the existing session with transcript-phase fields (log_path, branch_name, etc.) instead of creating a duplicate.

**Auto-continue reuse:** When `_enqueue_continuation()` fires, it reuses the existing session via delete-and-recreate rather than calling `enqueue_agent_session()` which would create a new orphaned record. This preserves all metadata automatically:
- `classification_type` (via `extra_context`)
- `session_events` (stage progress tracking)
- `issue_url`, `plan_url`, `pr_url` (link accumulation)
- `context_summary`, `expectations` (semantic routing)

Only four fields change during continuation: `status` (reset to "pending"), `initial_telegram_message.message_text` (nudge feedback), `auto_continue_count` (incremented), and `priority` (set to "high").

**Fresh reads in routing:** The `send_to_chat` closure in `_execute_agent_session()` re-reads the `AgentSession` from Redis before making routing decisions. This ensures `is_sdlc` and `stage_states` data are current, not the stale in-memory copy captured at session start.

### Field Preservation on Status Change

`status` is a Popoto `IndexedField`, so changing it only requires mutating the field and calling `.save()` — no delete-and-recreate needed.

## Per-Session Model Selection

`model` (`Field(null=True)`) stores the Claude model alias (e.g. `"opus"`,
`"sonnet"`, `"haiku"`) or full name (e.g. `"claude-opus-4-7"`) to use for this
session. The value flows end-to-end to the `claude -p` subprocess via the
CLI harness live path. The `ValorAgent → ClaudeAgentOptions` path from PR #909
was deleted wholesale in #2000 (see [HarnessAdapter Seam](harness-adapter.md));
there is no parallel SDK path to select between anymore.

### Precedence Cascade (D1)

The value that applies is the one explicitly set closest to the LLM call.
When nothing explicit is set, the cascade falls back with codebase defaults
at the bottom:

1. **`AgentSession.model`** (per-session, explicit) — set via
   `valor-session create --model <name>`, persisted on the record.
2. **`settings.models.session_default_model`** (machine-local override) —
   pydantic-settings field, env var `MODELS__SESSION_DEFAULT_MODEL`, sourced
   from `~/Desktop/Valor/.env` (iCloud-synced).
3. **Codebase default `"opus"`** — hard-coded as the pydantic `Field`
   default in `config/settings.py::ModelSettings`.

Implemented in `agent.session_executor._resolve_session_model()`:

```python
explicit = getattr(session, "model", None) if session else None
if explicit:
    return explicit
fallback = settings.models.session_default_model
return fallback or None
```

When the cascade resolves to `None` (operator set
`MODELS__SESSION_DEFAULT_MODEL=""`), `get_response_via_harness` omits the
`--model` flag and the Claude CLI uses its own default — graceful
degradation rather than a hard error.

### How It Flows

1. PM or human calls
   `python -m tools.valor_session create --role eng --slug {slug} --model sonnet --message "..."`.
2. `tools/valor_session.py::cmd_create` constructs the AgentSession via
   `enqueue_agent_session(model="sonnet")`; the value is persisted on the
   Redis record.
3. Worker pops the session; `agent/session_executor.py::_execute_agent_session`
   resolves the effective model via `_resolve_session_model(agent_session)`.
4. `agent/sdk_client.py::get_response_via_harness(model=...)` appends
   `["--model", <value>]` into `harness_cmd` (before positional `message`
   and before any `--resume <uuid>`).
5. Subprocess argv: `claude -p ... --model sonnet [--resume UUID] <message>`.
6. The Claude CLI honors the flag and the session runs on the requested
   model. INFO log `[harness] Using --model <value> for session_id=<id>`
   confirms the resolved value each turn.

### Override via `.env`

Operators can flip the default on a per-machine basis:

```bash
# ~/Desktop/Valor/.env
MODELS__SESSION_DEFAULT_MODEL=sonnet
```

Short aliases (`opus`/`sonnet`/`haiku`) are preferred; full version names
(`claude-opus-4-7`) also accepted and passed verbatim to the CLI.

### PM Final-Delivery Drafter (2-Pass, Always-Opus)

The PM-to-CEO final-delivery drafter in
`agent/session_completion.py::_deliver_pipeline_completion` is hardened as
a quality + reliability gate. It runs independently of the session cascade:

- **Always Opus** — both harness calls pin `model="opus"` regardless of the
  PM session's configured model. Quality trumps cost for this single call.
- **Two passes** — Pass 1 drafts from `summary_context`; Pass 2 reviews and
  refines Pass 1's draft against "short, dense, thoughtful" criteria. The
  refined text is the message the user receives.
- **No silent fail** — Pass 1 failures (empty, exception, or
  `Error: CLI harness not found` sentinel) log at ERROR and deliver a
  visible `[drafter unavailable — pipeline completed] <truncated context>`
  fallback message. Pass 2 failures log at WARNING and fall back to the
  Pass 1 draft. The `final_text` is guaranteed non-empty before `send_cb`.
- **Always finalize** — the session always transitions to `completed` (via
  `finalize_session` in a `finally` block) regardless of drafter or delivery
  outcome. Cancellation during drafter execution is the one exception: the
  shutdown path owns that transition via an "interrupted" message.
- **UUID isolation** — Pass 1 uses `session_id=None` so the drafter's
  Claude Code UUID is NOT written over the PM's `claude_session_uuid`.
  Pass 2 uses `prior_uuid=None` + `session_id=None` — the review prompt is
  self-contained (Pass 1's draft is embedded verbatim), so there's no need
  to resume the PM session and polluting its history is undesirable. Note
  this is passive isolation, not an active guard: the drafter path never
  reads or branches on `claude_session_uuid`; it simply declines to write by
  passing `session_id=None`. It therefore neither protects nor collides with
  the granite PM-handle mirror added in #1836.
- **Ollama fallback deferred** — see issue #1137. Until that lands,
  Anthropic-down manifests as a visible degraded-fallback message + ERROR
  log + Redis counter
  `completion_runner:degraded_fallback:daily:<YYYYMMDD>` (7-day TTL) so
  operators can detect outage spikes.

### Stage→Model Dispatch Table

The engineer persona's Stage→Model Dispatch Table
(`config/personas/engineer.md`) assigns Sonnet to BUILD/TEST/PATCH/DOCS and
Opus to PLAN/CRITIQUE/REVIEW. The parent eng session explicitly passes
`--model sonnet` (or `--model opus`) when spawning child sessions, which sets
`session.model` on the child's AgentSession record and wins the cascade. This
is the canonical way to vary models per stage — stage routing lives in the
engineer persona prose, NOT in settings.

See [pm-sdlc-decision-rules.md](pm-sdlc-decision-rules.md) for the full
stage table.

### Regression Guard

`tests/unit/test_harness_model_coverage.py` AST-walks `agent/*.py` and
fails any `get_response_via_harness(...)` call site that lacks a `model=`
kwarg — prevents the re-regression pattern that made PR #909's wiring
dormant on the worker path.

## BUILD Session Retention (`retain_for_resume`)

`retain_for_resume` (`Field(default=False)`) marks a completed BUILD session as exempt from scheduler
cleanup so the parent eng session can resume it later via `python -m tools.valor_session resume`.

**Lifecycle:**
1. The flag would be set on a completed BUILD child session to mark it for retention.
   **No code currently sets `retain_for_resume=True`.** The old setter,
   `_handle_dev_session_completion()`, was deleted in the PM+Dev → unified
   `eng` session merge, and no replacement setter was wired in. As of that
   merge the field is only ever read or cleared — see the flag below.
2. `tools/agent_session_scheduler.py cleanup` skips sessions where `retain_for_resume=True` and `status="completed"`.
   A log message is emitted each time a session is skipped so operators can audit retention.
3. PR merges/closes → the eng session calls `python -m tools.valor_session release --pr <N>` to clear the flag
   (`tools/valor_session.py` sets `retain_for_resume = False`).
4. `AgentSession.Meta.ttl = int(settings.timeouts.agent_session_retain_ttl_s)` (default 30 days / `2592000`s,
   `.env`-overridable via `TIMEOUTS__AGENT_SESSION_RETAIN_TTL_S` — see
   [Config Timeout Catalog](config-timeout-catalog.md)) is the hard backstop — sessions expire even if the release hook never fires.

**Current effective behavior:** with no setter, every completed BUILD session
defaults to `False` and is eligible for scheduler cleanup (bounded by the TTL).
The release path and the cleanup-skip guard remain in place for when a setter
is reintroduced, but the retain-on-completion behavior is presently inert.

**Default for pre-existing sessions:** `False` (backward-compatible — old BUILD sessions are not retained and will be
cleaned up by the TTL when they next touch Redis).

## Non-Executable Anchor (`is_ledger`)

`is_ledger` (`Field(default=False)`) marks a `sdlc-local-{N}` anchor session — created by `tools/sdlc_session_ensure.py` to give local `/do-sdlc` supervision somewhere to record stage markers and verdicts — as a non-executable ledger record. Eight worker recovery/pickup/scanner code paths check this flag and skip past the row instead of requeuing, finalizing, or running it, preventing a live worker from mistaking the anchor for orphaned work and racing the local supervisor on the same issue. See [Eng Session Architecture §sdlc-local session `is_ledger` non-executable flag (issue #2042)](eng-session-architecture.md#sdlc-local-session-is_ledger-non-executable-flag-issue-2042) for the full guard-site catalogue.

## Backward Compatibility

- `_normalize_kwargs()` maps old field names to their new consolidated equivalents: `message_text`, `sender_name`, `sender_id`, `telegram_message_id`, `chat_title` -> `initial_telegram_message`; `revival_context`, `classification_type`, `classification_confidence` -> `extra_context`; `work_item_slug` -> `slug`; `last_activity` -> `updated_at`; `scheduled_after` -> `scheduled_at`; `history` -> `session_events`; `watchdog_unhealthy` -> `unhealthy_reason` (schema diet, #1927)
- `__setattr__` auto-converts float timestamps to `datetime` for DatetimeField fields
- Property accessors provide read access to old field names (`sender_name`, `message_text`, etc.) for backward compatibility
- `models/agent_session.py` exports `AgentSession = AgentSession` (shim)
- No Redis data migration needed for new sessions; existing sessions can be migrated with `scripts/migrate_datetime_fields.py`

## Schema Diet (#1927)

By the time #1924 (PTY teardown) and #2000 (HarnessAdapter convergence onto a
single `claude -p` transport) had both landed, `AgentSession` still carried
roughly 2x the field surface its post-teardown meaning justified: fields with
no live writer kept around "to dodge a migration", write-only observability
counters with no production reader, and a metered/total token-accounting
split that existed only because a since-deleted PTY transcript-tailer and the
headless runner once wrote disjoint field sets concurrently. #1927 pruned
that surface down to fields with a live reader or writer (or a documented
keep-rationale) and applied one precision rename.

### Disposition table

| Field(s) | Disposition | Rationale |
|---|---|---|
| `self_report_sent_at` | **DELETE** | PM mid-work self-report retired 2026-05-06; no live writer |
| `sdk_connection_torn_down_at` | **DELETE** | Idle-sweeper substrate deleted by #2000; no live writer |
| `session_mode` | **DELETE** | Deprecated no-op since `session_type` became the discriminator |
| `pm_transcript_path`, `dev_transcript_path` | **DELETE** | No live writer; dashboard-only reads |
| `startup_failure_kind`, `startup_captured_frame` | **DELETE** | Historical PTY-era startup diagnostics; the entire `crash_signature.py` `ceiling` plumbing chain that read `startup_failure_kind` was removed too — see [Removed Defenses Ledger](../removed-defenses.md) |
| `compaction_count`, `compaction_skipped_count`, `nudge_deferred_count` | **CUT** | Write-only observability counters with no production reader — see [Compaction Hardening](compaction-hardening.md) |
| `metered_input_tokens`, `metered_output_tokens`, `metered_cache_read_tokens`, `metered_cost_usd` | **COLLAPSE** into `total_*` | See "Metered/total accounting collapse" below |
| `watchdog_unhealthy` | **RENAME** -> `unhealthy_reason` | Held a reason string, not a bool; the old name implied a flag — see [Session Health Check](session-health-check.md) and [Session Watchdog](session-watchdog.md) |
| `user_facing_routed` | **KEPT** (frozen scope) | Popoto's lazy-load bypasses `_normalize_kwargs`, so renaming it is unsafe: an in-flight session crossing a deploy boundary would read the renamed field as its `False` default and mis-fire the delivery emoji |
| `total_input_tokens`, `total_output_tokens`, `total_cache_read_tokens`, `total_cost_usd` | **KEPT** | High read fan-out (analytics, watchdog, tool budget, PM briefings) — renaming would be pure churn |

### Metered/total accounting collapse

`agent/sdk_client.py::accumulate_session_tokens` used to branch on a
`metered: bool` parameter: `metered=False` wrote the `total_*` scalars
(every non-session-runner caller), `metered=True` wrote a disjoint
`metered_*` field set for session-runner role turns (plan #1842) and emitted
a `session.metered_cost_usd` ledger metric. That split existed only to keep
the headless leg's additive writes from clobbering the PTY transcript
tailer's absolute `total_*` writes on a mixed-transport session. #2000
deleted the tailer, so every caller has written the same `total_*` fields
for the lifetime of a session since — the disjointness was already
vestigial. #1927 removed the `metered` parameter and both branches: there is
now exactly one write path, and every caller (the headless session-runner,
the completion drafter, probes) accumulates onto `total_*`. The dropped
`session.metered_cost_usd` ledger metric has no `total_*` replacement — an
accepted loss of longitudinal comparability, not an oversight.

### Migration

`scripts/migrate_schema_diet_fields.py` strips the deleted/renamed hash
fields from existing Redis records — see its module docstring for the full
field-by-field disposition. It follows the same ORM-safe, idempotent
delete+recreate pattern as `scripts/migrate_strip_pty_fields.py` (#1924):
only terminal-status records are rewritten (live rows are left alone and
age out via TTL), each rewrite happens on one transactional Redis pipeline,
and a second run reports zero stripped records. Registered in
`scripts/update/migrations.py` under the `schema_diet_fields` key so it runs
automatically as part of `/update`. Pre-cutover records that are never
migrated remain fully readable — Popoto ignores unknown hash fields on
load — so this migration reclaims storage; it is not required for
correctness.

## Migration

For existing Redis data with float timestamps or flat history strings:

```bash
# Preview changes
python scripts/migrate_datetime_fields.py --dry-run

# Run migration
python scripts/migrate_datetime_fields.py
```

## Related

- [Session Transcripts](session-transcripts.md) - Transcript file logging
- [Session Tagging](session-tagging.md) - Auto-tagging system
- [Message Drafter](message-drafter.md) - Drafter format and validation
- [Session Health Check](session-health-check.md) - Writer of `unhealthy_reason`
- [Session Watchdog](session-watchdog.md) - Reader of `unhealthy_reason`
- [Compaction Hardening](compaction-hardening.md) - The compaction counters cut by the schema diet
- [Removed Defenses Ledger](../removed-defenses.md) - `startup_failure_kind` plumbing removal record
