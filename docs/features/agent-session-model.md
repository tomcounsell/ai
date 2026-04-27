# AgentSession Model

Unified Redis model tracking agent work from enqueue through completion. Replaces both `AgentSession` (queue) and `SessionLog` (transcript) with a single `AgentSession` model in `models/agent_session.py`.

## Status Lifecycle

`pending` -> `running` -> `active` -> `dormant` -> `completed` | `failed` | `cancelled`

Additional non-terminal states: `waiting_for_children`, `superseded`, `paused_circuit`, `paused`.

The `cancelled` status is a terminal state set explicitly by the PM via `cancel_agent_session()`. Like `failed`, cancelled sessions block any sibling sessions that depend on them.

See [Session Lifecycle](session-lifecycle.md) for the full 13-state reference (8 non-terminal + 5 terminal).

## Key Fields

**Identity:** `id` (AutoKeyField), `session_id`, `session_type` (KeyField), `project_key` (KeyField), `chat_id` (KeyField), `status` (IndexedField). `agent_session_id` is a backward-compatible property alias for `id`.

**Queue-phase:** `priority`, `scheduled_at` (DatetimeField), `created_at` (SortedField, datetime), `started_at` (DatetimeField), `updated_at` (DatetimeField, auto_now), `completed_at` (DatetimeField), `auto_continue_count`

**Telegram origin (consolidated):** `initial_telegram_message` (DictField) — contains `sender_name`, `sender_id`, `message_text`, `telegram_message_id`, `chat_title`. Replaces the previous six separate fields. Property accessors (`sender_name`, `sender_id`, `message_text`) read from this dict for backward compatibility.

**Session-phase:** `turn_count`, `tool_call_count`, `log_path`, `branch_name`, `tags`, `session_mode`, `context_summary`, `expectations`

**Extra context (consolidated):** `extra_context` (DictField) — contains `revival_context`, `classification_type`, `classification_confidence`, and other ad-hoc context. Property accessors expose individual fields.

**Lifecycle:** `session_events` (ListField of `SessionEvent` dicts), `issue_url`, `plan_url`, `pr_url`

**Parent-Child:** `parent_agent_session_id` (KeyField — canonical parent reference), `role` (DataField — "pm", "dev", or null), `slug` (KeyField — derives branch, plan path, worktree; indexed so the slug-keyed worker-pop filter can find slugged dev sessions — see [Bridge/Worker Architecture §Three Worker Loop Archetypes](bridge-worker-architecture.md#three-worker-loop-archetypes))

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

String fields like `session_type`, `classification_type`, and `session_mode` use `StrEnum` members from `config/enums.py` (`SessionType`, `ClassificationType`, `ChatMode`). See [Standardized Enums](standardized-enums.md).

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
| 1 | `VALOR_SESSION_ID` env var | Bridge session_id, set by `sdk_client.py` |
| 2 | Direct `session_id` match | Works when caller has the bridge session_id |
| 3 | `task_list_id` match | Fallback for hook contexts with Claude Code UUID |

**Why three tiers?** Claude Code hooks receive Claude Code's internal UUID as `session_id`, which does not match the bridge's `AgentSession.session_id` (format: `tg_valor_{chat_id}_{msg_id}`). The hook session registry (see below) bridges this gap by giving hooks a direct path to the correct session. The `task_list_id` fallback provides belt-and-suspenders redundancy.

### VALOR_SESSION_ID Environment Variable

Set by `agent/sdk_client.py` in `_create_options()` alongside `CLAUDE_CODE_TASK_LIST_ID`. Propagated to all Claude Code subprocesses.

```python
# In sdk_client.py _create_options():
if session_id:
    env["VALOR_SESSION_ID"] = session_id
```

The env var is only set when `session_id` is non-None (i.e., when the SDK is invoked from the bridge with a real session). Local Claude Code sessions without bridge context will not have this env var set, and `_find_session()` falls back to the other lookup paths.

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
CLI harness live path (not the dormant `ValorAgent → ClaudeAgentOptions` path
from PR #909 — that wiring remains in place for unit-test fixtures but is
unreachable in production).

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
   `python -m tools.valor_session create --role dev --model sonnet --message "..."`.
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
  to resume the PM session and polluting its history is undesirable.
- **Ollama fallback deferred** — see issue #1137. Until that lands,
  Anthropic-down manifests as a visible degraded-fallback message + ERROR
  log + Redis counter
  `completion_runner:degraded_fallback:daily:<YYYYMMDD>` (7-day TTL) so
  operators can detect outage spikes.

### PM Stage Dispatch Table

The PM persona's Stage→Model Dispatch Table
(`config/personas/project-manager.md`) assigns Sonnet to
BUILD/TEST/PATCH/DOCS and Opus to PLAN/CRITIQUE/REVIEW. The PM explicitly
passes `--model sonnet` (or `--model opus`) when spawning Dev sessions,
which sets `session.model` on the Dev's AgentSession record and wins the
cascade. This is the canonical way to vary models per stage — stage
routing lives in PM persona prose, NOT in settings.

See [pm-sdlc-decision-rules.md](pm-sdlc-decision-rules.md) for the full
stage table.

### Regression Guard

`tests/unit/test_harness_model_coverage.py` AST-walks `agent/*.py` and
fails any `get_response_via_harness(...)` call site that lacks a `model=`
kwarg — prevents the re-regression pattern that made PR #909's wiring
dormant on the worker path.

## BUILD Session Retention (`retain_for_resume`)

`retain_for_resume` (`Field(default=False)`) marks a completed BUILD session as exempt from scheduler
cleanup so the PM can resume it later via `python -m tools.valor_session resume`.

**Lifecycle:**
1. BUILD dev session completes → `_handle_dev_session_completion()` sets `retain_for_resume=True`.
2. `tools/agent_session_scheduler.py cleanup` skips sessions where `retain_for_resume=True` and `status="completed"`.
   A log message is emitted each time a session is skipped so operators can audit retention.
3. PR merges/closes → PM calls `python -m tools.valor_session release --pr <N>` to clear the flag.
4. `AgentSession.Meta.ttl = 2592000` (30 days) is the hard backstop — sessions expire even if the release hook never fires.

**Default for pre-existing sessions:** `False` (backward-compatible — old BUILD sessions are not retained and will be
cleaned up by the TTL when they next touch Redis).

## Backward Compatibility

- `_normalize_kwargs()` maps old field names to their new consolidated equivalents: `message_text`, `sender_name`, `sender_id`, `telegram_message_id`, `chat_title` -> `initial_telegram_message`; `revival_context`, `classification_type`, `classification_confidence` -> `extra_context`; `work_item_slug` -> `slug`; `last_activity` -> `updated_at`; `scheduled_after` -> `scheduled_at`; `history` -> `session_events`
- `__setattr__` auto-converts float timestamps to `datetime` for DatetimeField fields
- Property accessors provide read access to old field names (`sender_name`, `message_text`, etc.) for backward compatibility
- `models/session_log.py` exports `SessionLog = AgentSession` (shim)
- No Redis data migration needed for new sessions; existing sessions can be migrated with `scripts/migrate_datetime_fields.py`

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
