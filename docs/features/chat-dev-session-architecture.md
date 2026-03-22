# ChatSession/DevSession Architecture

## Overview

The AgentSession model uses a **session_type discriminator** to distinguish between two session roles:

- **ChatSession** (`session_type="chat"`): Read-only Agent SDK session with PM persona. Owns the Telegram conversation, orchestrates work, and spawns DevSessions.
- **DevSession** (`session_type="dev"`): Full-permission Agent SDK session with Dev persona. Does the actual coding work and runs SDLC pipeline stages.

This replaces the previous architecture where a single undifferentiated AgentSession handled both orchestration and execution, with an external LLM-based Observer Agent making routing decisions between pipeline stages.

## Architecture

```
Telegram Message
    |
    v
ChatSession created (session_type="chat")
    |-- Queued per chat_id
    |-- Read-only, PM persona
    |-- Reads code, understands context
    |
    v
Spawns DevSession (session_type="dev")
    |-- Full permissions, Dev persona
    |-- Works full SDLC pipeline
    |-- Steered by ChatSession
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

## Deterministic Observer

The Observer Agent has been replaced with fully deterministic routing logic. No LLM calls are made for routing decisions.

### Routing Rules (in order)
1. **Rate limited** -> steer with backoff message
2. **Timeout** -> deliver to human
3. **Unknown stop reason** -> deliver to human
4. **Non-SDLC job** -> deliver immediately
5. **Output needs human input** (questions, fatal errors) -> deliver
6. **Failed pipeline stage** -> deliver
7. **Remaining stages exist** -> steer to next stage
8. **Pipeline complete** -> deliver

If the rules cannot determine an action, the output is delivered to the human. There is no LLM fallback.

## Queue Architecture

Jobs are queued per `project_key` (existing) with `session_type` passed through the queue. The queue supports both ChatSession and DevSession jobs.

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
| `bridge/observer.py` | Deterministic steer/deliver router |
| `agent/job_queue.py` | Queue with session_type support |
| `agent/sdk_client.py` | SDK client (classification from session, not re-classified) |

## Migration

- Old AgentSession records in Redis are compatible (session_type will be null, treated as legacy)
- No data migration needed -- old records are harmless
- Factory methods enforce field contracts for new sessions
- The Observer's circuit breaker functions are no-ops (backward compatible stubs)
