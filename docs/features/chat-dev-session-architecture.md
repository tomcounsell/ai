# ChatSession/DevSession Architecture

## Overview

The AgentSession model uses a **session_type discriminator** (`SessionType` enum from `config/enums.py`) to distinguish between two session roles:

- **ChatSession** (`session_type=SessionType.CHAT`): Read-only Agent SDK session with PM persona. Owns the Telegram conversation, orchestrates work, and spawns DevSessions.
- **DevSession** (`session_type=SessionType.DEV`): Full-permission Agent SDK session with Dev persona. Executes a single assigned SDLC stage and reports the result back to the PM.

Session types, persona identifiers, and classification types are defined as `StrEnum` members in `config/enums.py`. See [Standardized Enums](standardized-enums.md) for the full enum reference.

This replaces the previous architecture where a single undifferentiated AgentSession handled both orchestration and execution. The PM (ChatSession) now orchestrates the pipeline stage-by-stage, spawning one DevSession per stage.

## Routing

Messages are routed to session types via **config-driven chat mode resolution** (`resolve_chat_mode()` in `bridge/routing.py`), with title-prefix fallback for unconfigured groups:

1. **Config persona** -- if the project's `telegram.groups` dict has a matching entry with a `persona` field, it maps directly to a mode: `"developer"` -> dev, `"project-manager"` -> pm, `"teammate"` -> qa.
2. **Title prefix fallback** -- `"Dev: X"` -> dev mode, `"PM: X"` -> pm mode (backward compatible).
3. **DMs** -- always resolve to qa mode.

Session type derivation from resolved mode:

- **dev mode** -> `session_type="dev"` (DevSession, full permissions, dev persona). The classifier is skipped.
- **pm, qa, or unconfigured** -> `session_type="chat"` (ChatSession, PM persona). This includes both SDLC work and Q&A. The ChatSession decides whether to spawn a DevSession.

There are exactly two session types: `chat` and `dev`. The previous `simple` session type has been removed — all messages route through ChatSession, which is intelligent enough to handle Q&A directly. When a message is classified as an informational query with high confidence (>0.90), the ChatSession answers directly without spawning a DevSession. For groups with an explicit `"teammate"` persona, the classifier is bypassed entirely and Q&A mode is set directly. See [ChatSession Q&A Mode](chatsession-qa-mode.md) for the classifier design and [Config-Driven Chat Mode](config-driven-chat-mode.md) for the config schema and resolution order.

## Architecture

```
Telegram Message
    |
    v
resolve_chat_mode(project, chat_title, is_dm)
    |  1. Config persona lookup (telegram.groups.{name}.persona)
    |  2. Title prefix fallback (Dev:/PM:)
    |  3. DMs → always "qa"
    |
    |-- dev mode → DevSession (session_type="dev")
    |       |-- Full permissions, Dev persona
    |       |-- Direct execution
    |
    |-- pm/qa/None → ChatSession (session_type="chat")
            |-- Queued per chat_id
            |-- Read-only, PM persona
            |
            v
        Intent Classifier (Haiku, binary)
            |
            |-- Q&A (confidence > 0.90)
            |       |-- Direct answer with read-only tools
            |       |-- Reduced nudge cap (10)
            |       v
            |   Telegram Response
            |
            |-- Work (or low confidence)
                    |-- Stage-by-stage SDLC orchestration
                    |
                    v
                ChatSession assesses current stage
                    |-- Spawns one DevSession per stage
                    |-- Verifies result before progressing
                    |-- Repeats until pipeline complete
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
- `id` (AutoKeyField) -- primary key (aliased as `agent_session_id`)
- `session_id` -- Telegram-derived identifier
- `session_type` (KeyField) -- "chat" or "dev"
- `status` (KeyField) -- pending/running/active/dormant/completed/failed
- `project_key`, `created_at`, `history`, etc.

### ChatSession-specific fields
- `chat_id`, `message_id`, `sender_name`, `message_text` -- Telegram context
- `result_text` -- what was delivered to Telegram

### DevSession-specific fields
- `parent_session_id` (KeyField) -- logical FK to parent session (renamed from `parent_chat_session_id`)
- `role` (DataField) -- session specialization ("pm", "dev", or null for legacy)
- `stage_states` -- derived property reading from `session_events`
- `slug` -- derives branch name, plan path, worktree
- `issue_url`, `plan_url`, `pr_url` -- SDLC link URLs

### Session Creation
Sessions are created directly via `AgentSession.create(session_type="chat", ...)` or `AgentSession.create(session_type="dev", ...)`. The `_normalize_kwargs()` method handles backward-compatible field name mapping.

### Derived Properties
- `is_chat`, `is_dev` -- type checks
- `is_sdlc` -- derived from stage_states (not a stored flag)
- `current_stage` -- first stage with status "in_progress"
- `derived_branch_name` -- `session/{slug}` if slug exists
- `plan_path` -- `docs/plans/{slug}.md` if slug exists
- `summary`, `result_text`, `stage_states`, `last_commit_sha` -- derived from `session_events`
- `scheduling_depth` -- derived from parent chain walk (max depth 5)

## Nudge Loop (Bridge Output Routing)

The bridge uses a single nudge model for all output routing. No Observer, no SDLC stage awareness, no PipelineStateMachine in the bridge layer.

### How It Works

The bridge has ONE response to any non-completion: "Keep working -- only stop when you need human input or you're done."

ChatSession owns all SDLC intelligence. The bridge just keeps it working.

### Completion Detection
1. **Rate limited** -> wait with backoff, then nudge
2. **Empty output** -> nudge (not deliver)
3. **end_turn + substantial output** -> deliver to Telegram
4. **Safety cap** -> deliver regardless (50 nudges for work sessions, 10 for Q&A sessions via `qa_mode` flag)
5. **Already-completed session** -> deliver without nudge

### Key Constants
- `MAX_NUDGE_COUNT = 50` -- safety cap
- `NUDGE_MESSAGE` -- the single nudge text

## Queue Architecture

Jobs are queued per `chat_id` so different chat groups (even for the same project) can process jobs in parallel. Within a chat, jobs run sequentially to prevent git conflicts.

### Per-Chat Workers
- `_ensure_worker(chat_id)` -- starts a worker per chat
- `_worker_loop(chat_id)` -- processes jobs for a chat
- `_pop_agent_session(chat_id)` -- pops by chat_id
- Callbacks remain per `project_key` (Telegram client is project-scoped)

### Steering Messages
Human replies during active pipelines are buffered as steering messages on the ChatSession. The buffer is bounded at 10 messages (oldest dropped on overflow).

## Stage-by-Stage Orchestration

The PM (ChatSession) orchestrates SDLC work by spawning one DevSession per pipeline stage, rather than delegating the entire pipeline to a single DevSession.

### Flow

1. **PM assesses current stage** -- uses read-only Bash commands (gh, grep) to check what exists (issue, plan, PR, test status, review state)
2. **PM spawns one DevSession** -- dispatches a single-stage assignment with the Agent tool, including stage name, issue/PR URLs, current state, and acceptance criteria
3. **DevSession executes the assigned stage** -- runs the appropriate skill (/do-plan, /do-build, /do-test, etc.) and reports the result
4. **PM verifies the result** -- checks that the stage completed successfully
5. **PM repeats** -- assesses the next stage, spawns another DevSession, until the pipeline is complete or human input is needed

### Why Stage-by-Stage

- **Accountability**: Each stage result is verified before progressing
- **Visibility**: The PM can report intermediate progress to stakeholders
- **Recovery**: If a stage fails, the PM can re-dispatch or escalate without losing prior work
- **Judgment**: The PM decides whether trivial/docs-only work warrants the full pipeline

### Completion Warning

The stop hook (`.claude/hooks/stop.py`) includes a warning for SDLC-classified sessions that complete without any stage progress. This catches cases where the DevSession bypasses the pipeline. The warning is logged to stderr and is non-fatal.

## Parent-Child Steering

The ChatSession can push steering messages to its running child DevSessions, enabling mid-execution course correction without waiting for the DevSession to complete.

### Mechanism

ChatSession invokes `scripts/steer_child.py` via bash with the child's session ID and a steering message. The script validates the parent-child relationship (via `parent_session_id`) and pushes to the child's Redis steering queue. The child's watchdog hook picks up the message on the next tool call.

```bash
# Steer a running child
python scripts/steer_child.py --session-id <child_id> --message "focus on tests" --parent-id <parent_id>

# Abort a child
python scripts/steer_child.py --session-id <child_id> --message "stop" --parent-id <parent_id> --abort

# List active children
python scripts/steer_child.py --list --parent-id <parent_id>
```

This reuses the same steering infrastructure (Redis queue, watchdog consumption) as Telegram reply-thread steering. See [Steering Queue](steering-queue.md) for the full steering architecture.

## Q&A Formatting (Prose vs Structured)

The PM persona uses different output formatting for Q&A sessions versus work sessions. The `qa_mode` flag on `AgentSession` is the single branch point for all formatting differences.

### Q&A Mode (conversational prose)

When `qa_mode=True`:
- **Instructions**: `build_teammate_instructions()` in `agent/teammate_handler.py` emphasizes research-first behavior -- search code, query memory, consult docs, cite findings
- **Summarizer**: The summarizer LLM receives `qa_mode=True` context and produces conversational prose instead of bullets
- **Structured summary bypass**: `_compose_structured_summary()` in `bridge/summarizer.py` returns the LLM summary directly without emoji prefix, bullet parsing, or structured template
- **Reaction**: Processing reaction is cleared (set to `None`) after Q&A delivery instead of setting a completion emoji
- **Single delivery path**: Q&A always goes through the summarizer -- no dual-path ambiguity with `send_telegram.py`

### Work Mode (structured formatting)

When `qa_mode=False` or unset:
- **Summarizer**: Produces bullet points with status emoji prefix
- **Structured summary**: Full formatting with emoji, stage line (for SDLC), bullets, question section, and link footer
- **Reaction**: Completion emoji set on success

### Data Flow

```
Q&A message → intent classifier → qa_mode=True on AgentSession
    → Agent researches (code, memory, docs)
    → Agent returns prose answer
    → Summarizer formats as prose (qa_mode context)
    → _compose_structured_summary() bypasses structured template
    → Telegram delivers prose directly
    → Processing reaction cleared (None)
```

## Agent Definitions

The `dev-session` agent is defined in `agent/agent_definitions.py`:
- `tools=None` (all tools, full write permissions)
- `model=None` (inherits from parent session)
- Single-stage executor: receives a stage assignment from the PM, executes it, reports result
- Spawned by ChatSession via the Agent tool

## Key Files

| File | Purpose |
|------|---------|
| `models/agent_session.py` | AgentSession model with session_type discriminator |
| `agent/agent_definitions.py` | Agent registry including dev-session |
| `agent/agent_session_queue.py` | Queue with nudge loop and per-chat workers |
| `agent/sdk_client.py` | SDK client (classification from session, not re-classified) |

## Migration

- Older AgentSession records in Redis are compatible (session_type defaults to null)
- Float timestamps are auto-converted to datetime via `__setattr__`; run `scripts/migrate_datetime_fields.py` for existing data
- `_normalize_kwargs()` maps deprecated field names to consolidated equivalents
- Workers auto-adapt: jobs with chat_id use per-chat routing; older jobs fall back to project_key
