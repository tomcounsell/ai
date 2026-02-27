# AgentSession Model

Unified Redis model tracking agent work from enqueue through completion. Replaces both `RedisJob` (queue) and `SessionLog` (transcript) with a single `AgentSession` model in `models/agent_session.py`.

## Status Lifecycle

`pending` -> `running` -> `active` -> `dormant` -> `completed` | `failed`

## Key Fields

**Queue-phase:** `job_id`, `project_key`, `status`, `priority`, `message_text`, `sender_name`, `chat_id`, `message_id`, `auto_continue_count`, `started_at`

**Session-phase:** `turn_count`, `tool_call_count`, `log_path`, `summary`, `branch_name`, `tags`, `classification_type`

**New:** `history` (ListField, append-only lifecycle events), `issue_url`, `plan_url`, `pr_url`

## History Tracking

`append_history(role, text)` records lifecycle events capped at 20 entries:
- `[user]` - Original request
- `[classify]` - Auto-classification result
- `[stage]` - SDLC stage transitions (e.g., `BUILD ☑`)
- `[summary]` - Session summary notes

## SDLC Stage Helpers

Methods for querying pipeline state from history:

| Method | Returns | Purpose |
|---|---|---|
| `is_sdlc_job()` | `bool` | `True` if history contains any `[stage]` entries |
| `has_remaining_stages()` | `bool` | `True` if any SDLC stage is `pending` or `in_progress` |
| `has_failed_stage()` | `bool` | `True` if any stage has `FAILED` or `ERROR` status |
| `get_stage_progress()` | `dict` | Maps stage names to status (`completed`, `in_progress`, `pending`, `failed`) |

These are used by the [stage-aware auto-continue](bridge-workflow-gaps.md#stage-aware-path-sdlc-jobs) routing in `agent/job_queue.py`.

## Link Accumulation

`set_link(kind, url)` stores issue, plan, and PR URLs as each SDLC stage completes. `get_links()` returns all tracked links.

## task_list_id Lifecycle

The `task_list_id` field bridges the gap between Claude Code's hook system and AgentSession lookup. Hooks fire with Claude Code's internal UUID as the session ID, which does not match the Telegram-style `session_id` stored on the AgentSession. The `task_list_id` field provides the secondary lookup path.

**When task_list_id is set:**

1. Job is enqueued via `_push_job()` in `agent/job_queue.py` -- if the caller provides a `task_list_id`, it is stored at creation time
2. During `_execute_job()`, the computed `task_list_id` (derived from `work_item_slug` or `thread-{chat_id}-{root_msg_id}`) is written to the AgentSession and saved

**How hooks resolve sessions:**

1. `tools/session_progress.py` calls `_find_session(session_id)` with whatever ID the hook provides
2. First attempt: direct `session_id` match via `AgentSession.query.filter(session_id=...)`
3. Fallback: scan all sessions and match on `task_list_id`

**Why both paths are needed:**

- SDLC skills running inside Claude Code fire hooks with Claude Code's internal session UUID
- This UUID does not match the Telegram-style `session_id` (e.g., `telegram_chat_12345_msg_67890`)
- The `task_list_id` is injected as `CLAUDE_CODE_TASK_LIST_ID` env var into Claude Code by `sdk_client.py`, so hooks can pass it to `session_progress.py`
- The fallback scan in `_find_session()` resolves the UUID to the correct AgentSession

## Field Preservation During Status Changes

Popoto's `KeyField` (used for `status`) cannot be mutated in place -- the only way to change a KeyField value is delete-and-recreate. The `complete_transcript()` function in `bridge/session_transcript.py` handles this by:

1. Dynamically extracting ALL model fields via `AgentSession._meta.fields`
2. Skipping `job_id` (AutoKeyField, auto-generated) and `status` (being changed)
3. Overriding `completed_at`, `last_activity`, and optionally `summary`
4. Deleting the old record and creating a new one with the updated status

This dynamic extraction ensures new fields added to the model are automatically preserved without requiring manual updates to the field list.

The same pattern is used in `_pop_job()` and `_recover_interrupted_jobs()` in `agent/job_queue.py`, which use `_extract_job_fields()` (based on the `_JOB_FIELDS` constant) for the same purpose.

## Single Session Creation

Each message produces exactly one AgentSession. The creation flow is:

1. `_push_job()` creates the AgentSession with `status="pending"` at enqueue time
2. `_pop_job()` converts to `status="running"` via delete-and-recreate
3. `start_transcript()` finds the existing session and updates `log_path`, `started_at`, `last_activity` -- it does NOT create a new session

This prevents orphaned duplicate sessions that previously occurred when both `_push_job()` and `start_transcript()` each called `AgentSession.create()`.

## CLI Tool

`tools/session_progress.py` updates stage progress and links from Bash:

```bash
python -m tools.session_progress --session-id $ID --stage BUILD --status completed
python -m tools.session_progress --session-id $ID --pr-url https://github.com/.../pull/42
```

### SDLC Skill Wiring

Each SDLC skill calls `session_progress.py` to record stage transitions. The SESSION_ID is extracted from the `SESSION_ID: xxx` line injected by `sdk_client.py` into enriched messages.

| Skill | Stage | Transitions | Links Set |
|-------|-------|-------------|-----------|
| `/sdlc` | ISSUE | `completed` after issue verified | `issue-url` |
| `/do-plan` | PLAN | `in_progress` → `completed` | `plan-url` |
| `/do-build` | BUILD | `in_progress` → `completed` | `pr-url` |
| `/do-test` | TEST | `in_progress` → `completed` or `failed` | — |
| `/do-pr-review` | REVIEW | `in_progress` → `completed` | — |
| `/do-docs` | DOCS | `in_progress` → `completed` | — |

All calls use `2>/dev/null || true` for fire-and-forget behavior — stage tracking failures never block pipeline work.

### Error Handling

- `_find_session()` catches Redis connection errors and returns `None`
- `_find_session()` falls back to `task_list_id` scan when `session_id` match fails
- `main()` exits 0 when no session is found (fire-and-forget)
- Debug logging on `append_history()`, `set_link()`, `get_stage_progress()` via `logging.getLogger(__name__)`

## Backward Compatibility

- `models/session_log.py` exports `SessionLog = AgentSession` (shim)
- `agent/job_queue.py` exports `RedisJob = AgentSession` (alias)
- No Redis data migration needed - old keys age out via TTL

## Related

- [Session Transcripts](session-transcripts.md) - Transcript file logging
- [Session Tagging](session-tagging.md) - Auto-tagging system
- [Summarizer Format](summarizer-format.md) - Bullet-point summaries
