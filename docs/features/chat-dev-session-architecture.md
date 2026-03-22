# ChatSession/DevSession Architecture

## Overview

The AgentSession model uses a **session_type discriminator** to distinguish between two session roles:

- **ChatSession** (`session_type="chat"`): Read-only Agent SDK session with PM persona. Owns the Telegram conversation, orchestrates work, and spawns DevSessions.
- **DevSession** (`session_type="dev"`): Full-permission Agent SDK session with Dev persona. Does the actual coding work and runs SDLC pipeline stages.

This replaces the previous architecture where a single undifferentiated AgentSession handled both orchestration and execution, with an external LLM-based Observer Agent making routing decisions between pipeline stages.

## Routing

Messages are routed to session types based on chat title prefix:

- **"Dev: X" groups** -> `session_type="dev"` (DevSession, full permissions, dev persona). The classifier is skipped — Dev groups always get a DevSession directly.
- **Everything else** -> `session_type="chat"` (ChatSession, PM persona). This includes both SDLC work and Q&A. The ChatSession decides whether to spawn a DevSession.

There are exactly two session types: `chat` and `dev`. The previous `simple` session type has been removed — all messages route through ChatSession, which is intelligent enough to handle Q&A directly.

## Architecture

```
Telegram Message
    |
    v
Route by chat_title prefix
    |
    |-- "Dev: X" → DevSession (session_type="dev")
    |       |-- Full permissions, Dev persona
    |       |-- Direct execution
    |
    |-- Everything else → ChatSession (session_type="chat")
            |-- Queued per chat_id
            |-- Read-only, PM persona
            |-- May spawn DevSession for SDLC work
            |
            v
        ChatSession composes delivery
            |-- Persona-voiced message
            v
        Telegram Response
```

## Data Model

Single Popoto model (`AgentSession`) with discriminator field. Popoto ORM does not support model inheritance, so both types share one model with nullable type-specific fields.

### Shared fields (all sessions)
- `job_id` (AutoKeyField) -- primary key
- `session_id` -- Telegram-derived identifier
- `session_type` (KeyField) -- "chat" or "dev"
- `status` (KeyField) -- pending/running/active/dormant/completed/failed
- `project_key`, `created_at`, `history`, etc.

### ChatSession-specific fields
- `chat_id`, `message_id`, `sender_name`, `message_text` -- Telegram context
- `result_text` -- what was delivered to Telegram

### DevSession-specific fields
- `parent_chat_session_id` (KeyField) -- logical FK to parent ChatSession
- `sdlc_stages` -- JSON dict of stage -> status
- `slug` -- derives branch name, plan path, worktree
- `artifacts` -- JSON dict of issue_url, plan_url, pr_url, etc.

### Factory Methods
- `AgentSession.create_chat(...)` -- creates a ChatSession with correct defaults
- `AgentSession.create_dev(...)` -- creates a DevSession linked to a parent

### Derived Properties
- `is_chat`, `is_dev` -- type checks
- `is_sdlc` -- derived from sdlc_stages (not a stored flag)
- `current_stage` -- first stage with status "in_progress"
- `derived_branch_name` -- `session/{slug}` if slug exists
- `plan_path` -- `docs/plans/{slug}.md` if slug exists

## Nudge Loop (Bridge Output Routing)

The bridge uses a single nudge model for all output routing. No Observer, no SDLC stage awareness, no PipelineStateMachine in the bridge layer.

### How It Works

The bridge has ONE response to any non-completion: "Keep working -- only stop when you need human input or you're done."

ChatSession owns all SDLC intelligence. The bridge just keeps it working.

### Completion Detection
1. **Rate limited** -> wait with backoff, then nudge
2. **Empty output** -> nudge (not deliver)
3. **end_turn + substantial output** -> deliver to Telegram
4. **Safety cap (50 nudges)** -> deliver regardless
5. **Already-completed session** -> deliver without nudge

### Key Constants
- `MAX_NUDGE_COUNT = 50` -- safety cap
- `NUDGE_MESSAGE` -- the single nudge text

## Queue Architecture

Jobs are queued per `chat_id` so different chat groups (even for the same project) can process jobs in parallel. Within a chat, jobs run sequentially to prevent git conflicts.

### Per-Chat Workers
- `_ensure_worker(chat_id)` -- starts a worker per chat
- `_worker_loop(chat_id)` -- processes jobs for a chat
- `_pop_job(chat_id)` -- pops by chat_id
- Callbacks remain per `project_key` (Telegram client is project-scoped)

### Steering Messages
Human replies during active pipelines are buffered as steering messages on the ChatSession. The buffer is bounded at 10 messages (oldest dropped on overflow).

## Agent Definitions

The `dev-session` agent is defined in `agent/agent_definitions.py`:
- `tools=None` (all tools, full write permissions)
- `model=None` (inherits from parent session)
- Spawned by ChatSession via the Agent tool

## Key Files

| File | Purpose |
|------|---------|
| `models/agent_session.py` | AgentSession model with session_type discriminator |
| `agent/agent_definitions.py` | Agent registry including dev-session |
| `agent/job_queue.py` | Queue with nudge loop and per-chat workers |
| `agent/sdk_client.py` | SDK client (classification from session, not re-classified) |

## Migration

- Old AgentSession records in Redis are compatible (session_type will be null, treated as legacy)
- No data migration needed -- old records are harmless
- Factory methods enforce field contracts for new sessions
- Workers auto-adapt: jobs with chat_id use per-chat routing; legacy jobs fall back to project_key
