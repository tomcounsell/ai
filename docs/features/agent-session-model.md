# AgentSession Model

Unified Redis model tracking agent work from enqueue through completion. Replaces both `RedisJob` (queue) and `SessionLog` (transcript) with a single `AgentSession` model in `models/agent_session.py`.

## Status Lifecycle

`pending` -> `running` -> `active` -> `dormant` -> `completed` | `failed`

## Key Fields

**Queue-phase:** `job_id`, `project_key`, `status`, `priority`, `message_text`, `sender_name`, `chat_id`, `message_id`, `auto_continue_count`, `started_at`

**Session-phase:** `turn_count`, `tool_call_count`, `log_path`, `summary`, `branch_name`, `tags`, `classification_type`

**Semantic routing:** `context_summary` (what the session is about), `expectations` (what the agent needs from the human)

**New:** `history` (ListField, append-only lifecycle events), `issue_url`, `plan_url`, `pr_url`

## History Tracking

`append_history(role, text)` records lifecycle events capped at 20 entries. When truncation occurs, a `WARNING`-level log is emitted with the original length and number of dropped entries:
- `[user]` - Original request
- `[classify]` - Auto-classification result
- `[summary]` - Session summary notes

## SDLC Stage Tracking

Pipeline stage state is stored in the `stage_states` JSON field on AgentSession, managed by the `PipelineStateMachine` in `bridge/pipeline_state.py`. The state machine provides:

| Method | Returns | Purpose |
|---|---|---|
| `PipelineStateMachine.has_remaining_stages()` | `bool` | `True` if pipeline graph has a non-terminal next stage from the last completed stage |
| `PipelineStateMachine.has_failed_stage()` | `bool` | `True` if any stage has `FAILED` or `ERROR` status |
| `PipelineStateMachine.get_display_progress()` | `dict` | Maps stage names to status (`completed`, `in_progress`, `pending`, `failed`) |

`is_sdlc` (property) returns `True` if `classification_type == "sdlc"`.

These are used by the [stage-aware auto-continue](bridge-workflow-gaps.md#stage-aware-path-sdlc-jobs) routing in `agent/job_queue.py`.

## Link Accumulation

`set_link(kind, url)` stores issue, plan, and PR URLs as each SDLC stage completes. `get_links()` returns all tracked links.

## Stage Tracking

Stage transitions are managed by the `PipelineStateMachine` in `bridge/pipeline_state.py`. Stage status is set programmatically at transition points (`start_stage()`, `complete_stage()`, `fail_stage()`) rather than via a CLI tool.

| Skill | Stage | Transitions | Links Set |
|-------|-------|-------------|-----------|
| `/sdlc` | ISSUE | `completed` after issue verified | `issue-url` |
| `/do-plan` | PLAN | `in_progress` → `completed` | `plan-url` |
| `/do-build` | BUILD | `in_progress` → `completed` | `pr-url` |
| `/do-test` | TEST | `in_progress` → `completed` or `failed` | — |
| `/do-pr-review` | REVIEW | `in_progress` → `completed` | — |
| `/do-docs` | DOCS | `in_progress` → `completed` | — |

### Session Lookup Chain

`_find_session()` resolves an AgentSession using a three-tier lookup:

| Priority | Source | Description |
|----------|--------|-------------|
| 1 | `VALOR_SESSION_ID` env var | Bridge session_id, set by `sdk_client.py` |
| 2 | Direct `session_id` match | Works when caller has the bridge session_id |
| 3 | `task_list_id` match | Fallback for hook contexts with Claude Code UUID |

**Why three tiers?** Claude Code hooks receive Claude Code's internal UUID as `session_id`, which does not match the bridge's `AgentSession.session_id` (format: `tg_valor_{chat_id}_{msg_id}`). The `VALOR_SESSION_ID` env var bridges this gap by giving hooks a direct path to the correct session. The `task_list_id` fallback provides belt-and-suspenders redundancy.

### VALOR_SESSION_ID Environment Variable

Set by `agent/sdk_client.py` in `_create_options()` alongside `CLAUDE_CODE_TASK_LIST_ID`. Propagated to all Claude Code subprocesses including hooks.

```python
# In sdk_client.py _create_options():
if session_id:
    env["VALOR_SESSION_ID"] = session_id
```

The env var is only set when `session_id` is non-None (i.e., when the SDK is invoked from the bridge with a real session). Local Claude Code sessions without bridge context will not have this env var set, and `_find_session()` falls back to the other lookup paths.

### task_list_id Persistence

`task_list_id` is computed in `_execute_job()` and persisted to the `AgentSession` immediately after the session is found:

- **Tier 1 (ad-hoc):** `thread-{chat_id}-{root_msg_id}`
- **Tier 2 (planned work):** The work item slug (e.g., `bridge-sdk-fix`)

This ensures hooks can resolve sessions via `task_list_id` even if `VALOR_SESSION_ID` is not available.

### Error Handling

- `_find_session()` catches Redis connection errors and returns `None`
- `main()` exits 0 when no session is found (fire-and-forget)
- Debug logging on `append_history()`, `set_link()`, `get_stage_progress()` via `logging.getLogger(__name__)`
- WARNING-level logging on `append_history()` and `set_link()` save failures, including operation context (role, field name)

## Session Lifecycle Integrity

### Single-Session Guarantee (Source of Truth Architecture)

Each `session_id` has exactly one `AgentSession` at any time. The `AgentSession` is the single source of truth for all session metadata -- no state needs to be passed as parameters between functions.

**Session creation:** `_push_job()` creates the session at enqueue time. `start_transcript()` updates the existing session with transcript-phase fields (log_path, branch_name, etc.) instead of creating a duplicate.

**Auto-continue reuse:** When `_enqueue_continuation()` fires, it reuses the existing session via delete-and-recreate rather than calling `enqueue_job()` which would create a new orphaned record. This preserves all metadata automatically:
- `classification_type` (SDLC routing decisions)
- `history` (stage progress tracking)
- `issue_url`, `plan_url`, `pr_url` (link accumulation)
- `context_summary`, `expectations` (semantic routing)

Only four fields change during continuation: `status` (reset to "pending"), `message_text` (coaching message), `auto_continue_count` (incremented), and `priority` (set to "high").

**Fresh reads in routing:** The `send_to_chat` closure in `_execute_job()` re-reads the `AgentSession` from Redis before making routing decisions. This ensures `is_sdlc` and `stage_states` data are current, not the stale in-memory copy captured at job start.

### Field Preservation on Status Change

`status` is a Popoto `KeyField`, so changing it requires delete-and-recreate. The `_JOB_FIELDS` list in `agent/job_queue.py` enumerates all fields that must be preserved during delete-and-recreate operations. This list must be kept in sync with `AgentSession` model fields -- missing fields are silently dropped.

Fields preserved: all queue-phase fields, session-phase fields, semantic routing fields (`context_summary`, `expectations`), history, and link URLs. The `_extract_job_fields()` helper reads every field in `_JOB_FIELDS` from the old session and passes them to `async_create()` on the new one.

## Backward Compatibility

- `models/session_log.py` exports `SessionLog = AgentSession` (shim)
- `agent/job_queue.py` exports `RedisJob = AgentSession` (alias)
- No Redis data migration needed - old keys age out via TTL

## Related

- [Session Transcripts](session-transcripts.md) - Transcript file logging
- [Session Tagging](session-tagging.md) - Auto-tagging system
- [Summarizer Format](summarizer-format.md) - Bullet-point summaries
