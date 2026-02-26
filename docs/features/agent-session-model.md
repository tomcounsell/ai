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

## CLI Tool

`tools/session_progress.py` updates stage progress and links from Bash:

```bash
python -m tools.session_progress --session-id $ID --stage BUILD --status completed
python -m tools.session_progress --session-id $ID --pr-url https://github.com/.../pull/42
```

## Backward Compatibility

- `models/session_log.py` exports `SessionLog = AgentSession` (shim)
- `agent/job_queue.py` exports `RedisJob = AgentSession` (alias)
- No Redis data migration needed - old keys age out via TTL

## Related

- [Session Transcripts](session-transcripts.md) - Transcript file logging
- [Session Tagging](session-tagging.md) - Auto-tagging system
- [Summarizer Format](summarizer-format.md) - Bullet-point summaries
