# AgentSession Model

Unified Redis model tracking agent work from enqueue through completion. Replaces both `AgentSession` (queue) and `SessionLog` (transcript) with a single `AgentSession` model in `models/agent_session.py`.

## Status Lifecycle

`pending` -> `running` -> `active` -> `dormant` -> `completed` | `failed` | `cancelled`

The `cancelled` status is a terminal state set explicitly by the PM via `cancel_agent_session()`. Like `failed`, cancelled sessions block any sibling sessions that depend on them.

## Key Fields

**Identity:** `id` (AutoKeyField), `session_id`, `session_type` (KeyField), `project_key` (KeyField), `chat_id` (KeyField), `status` (IndexedField). `agent_session_id` is a backward-compatible property alias for `id`.

**Queue-phase:** `priority`, `scheduled_at` (DatetimeField), `created_at` (SortedField, datetime), `started_at` (DatetimeField), `updated_at` (DatetimeField, auto_now), `completed_at` (DatetimeField), `auto_continue_count`

**Telegram origin (consolidated):** `initial_telegram_message` (DictField) — contains `sender_name`, `sender_id`, `message_text`, `telegram_message_id`, `chat_title`. Replaces the previous six separate fields. Property accessors (`sender_name`, `sender_id`, `message_text`) read from this dict for backward compatibility.

**Session-phase:** `turn_count`, `tool_call_count`, `log_path`, `branch_name`, `tags`, `session_mode`, `context_summary`, `expectations`

**Extra context (consolidated):** `extra_context` (DictField) — contains `revival_context`, `classification_type`, `classification_confidence`, and other ad-hoc context. Property accessors expose individual fields.

**Lifecycle:** `session_events` (ListField of `SessionEvent` dicts), `issue_url`, `plan_url`, `pr_url`

**Parent-Child:** `parent_agent_session_id` (KeyField — canonical parent reference), `parent_session_id` and `parent_chat_session_id` (deprecated `@property` aliases delegating to `parent_agent_session_id`), `role` (DataField — "pm", "dev", or null), `slug`

All timestamp fields use Popoto `DatetimeField` or `SortedField(type=datetime)` with proper UTC datetime objects. Float timestamps are auto-converted via `__setattr__`.

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

Pipeline stage state is stored in `session_events` (as `stage` type events) on AgentSession, managed by the `PipelineStateMachine` in `bridge/pipeline_state.py`. The `stage_states` property reads the latest stage event's data and returns the stages dict. The state machine provides:

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

Stage transitions are managed by the `PipelineStateMachine` in `bridge/pipeline_state.py`. Stage status is set programmatically at transition points (`start_stage()`, `complete_stage()`, `fail_stage()`) rather than via a CLI tool.

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

## Backward Compatibility

- `_normalize_kwargs()` maps deprecated field names to their new consolidated equivalents: `message_text`, `sender_name`, `sender_id`, `telegram_message_id`, `chat_title` -> `initial_telegram_message`; `revival_context`, `classification_type`, `classification_confidence` -> `extra_context`; `work_item_slug` -> `slug`; `last_activity` -> `updated_at`; `scheduled_after` -> `scheduled_at`; `history` -> `session_events`
- `__setattr__` auto-converts float timestamps to `datetime` for DatetimeField fields
- Property accessors provide read access to legacy field names (`sender_name`, `message_text`, etc.)
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
- [Summarizer Format](summarizer-format.md) - Bullet-point summaries
