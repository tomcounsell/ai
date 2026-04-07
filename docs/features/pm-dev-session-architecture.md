# PM/Teammate/Dev session Architecture

## Overview

The AgentSession model uses a **session_type discriminator** (`SessionType` enum from `config/enums.py`) to distinguish between three session roles:

- **PM Session** (`session_type=SessionType.PM`): Read-only Agent SDK session with PM persona. Owns the Telegram conversation, orchestrates work, and spawns Dev sessions.
- **Teammate Session** (`session_type=SessionType.TEAMMATE`): Conversational Agent SDK session with Teammate persona. Handles informational queries directly without spawning Dev sessions.
- **Dev session** (`session_type=SessionType.DEV`): Full-permission Agent SDK session with Dev persona. Executes a single assigned SDLC stage and reports the result back to the PM.

Session types, persona identifiers, and classification types are defined as `StrEnum` members in `config/enums.py`. See [Standardized Enums](standardized-enums.md) for the full enum reference.

This replaces the previous architecture where a single undifferentiated AgentSession handled both orchestration and execution. The PM session now orchestrates the pipeline stage-by-stage, spawning one Dev session per stage.

## Routing

Messages are routed to session types via **config-driven persona resolution** (`resolve_persona()` in `bridge/routing.py`), with title-prefix fallback for unconfigured groups:

1. **Config persona** -- if the project's `telegram.groups` dict has a matching entry with a `persona` field, it maps directly: `"developer"` -> dev, `"project-manager"` -> pm, `"teammate"` -> teammate.
2. **Title prefix fallback** -- `"Dev: X"` -> dev mode, `"PM: X"` -> pm mode (backward compatible).
3. **DMs** -- always resolve to teammate mode.

Session type derivation from resolved persona:

- **Developer persona** -> `session_type="dev"` (Dev session, full permissions, dev persona). The classifier is skipped.
- **Teammate persona** -> `session_type="teammate"` (Teammate session, conversational). Handles informational queries directly.
- **PM, or unconfigured** -> `session_type="pm"` (PM session, PM persona). This includes SDLC work. The PM session decides whether to spawn a Dev session.

There are exactly three session types: `pm`, `teammate`, and `dev`. The previous `chat` session type has been renamed to `pm` and `teammate` has been promoted from a secondary `session_mode` flag to a first-class session type. See [Config-Driven Chat Mode](config-driven-chat-mode.md) for the config schema and resolution order.

## Architecture

```
Telegram Message
    |
    v
resolve_persona(project, chat_title, is_dm)
    |  1. Config persona lookup (telegram.groups.{name}.persona)
    |  2. Title prefix fallback (Dev:/PM:)
    |  3. DMs -> always "teammate"
    |
    |-- Developer -> Dev session (session_type="dev")
    |       |-- Full permissions, Dev persona
    |       |-- Direct execution
    |
    |-- Teammate -> Teammate Session (session_type="teammate")
    |       |-- Conversational, Teammate persona
    |       |-- Direct answer with read-only tools
    |       |-- Reduced nudge cap (10)
    |       v
    |   Telegram Response
    |
    |-- PM/None -> PM Session (session_type="pm")
            |-- Queued per chat_id
            |-- Read-only, PM persona
            |
            v
        Intent Classifier (Haiku, binary)
            |
            |-- Work (or low confidence)
                    |-- Stage-by-stage SDLC orchestration
                    |
                    v
                PM assesses current stage
                    |-- Spawns one Dev session per stage
                    |-- Verifies result before progressing
                    |-- Repeats until pipeline complete
                    |
                    v
                PM composes delivery
                    |-- Persona-voiced message
                    v
                Telegram Response
```

## Data Model

Single Popoto model (`AgentSession`) with discriminator field. Popoto ORM does not support model inheritance, so all types share one model with nullable type-specific fields.

### Shared fields (all sessions)
- `id` (AutoKeyField) -- primary key (aliased as `agent_session_id`)
- `session_id` -- Telegram-derived identifier
- `session_type` (KeyField) -- "pm", "teammate", or "dev"
- `status` (KeyField) -- pending/running/active/dormant/completed/failed
- `project_key`, `created_at`, `history`, etc.
- `project_config` (DictField) -- full project dict from `projects.json`, populated at enqueue time. Carries all project properties (name, working_directory, github, mode, telegram, etc.) through the pipeline so downstream code never re-derives from a parallel registry. Empty/None for legacy sessions created before this field existed; the worker falls back to loading from `projects.json` at execution time.

### PM/Teammate session-specific fields
- `chat_id`, `message_id`, `sender_name`, `message_text` -- Telegram context
- `result_text` -- what was delivered to Telegram

### Dev session-specific fields
- `parent_agent_session_id` (KeyField) -- **canonical** parent link (role-neutral). Set by all session creators (`create_child`, `create_dev`, `enqueue_session`) and read by all hierarchy walkers (`scheduling_depth`, `get_parent_session`, `get_child_sessions`, the zombie health check, the dashboard).
- `parent_session_id` -- **deprecated** `@property` alias delegating to `parent_agent_session_id`. Kept for one release cycle. New code should use `parent_agent_session_id` directly.
- `parent_chat_session_id` -- **deprecated** `@property` alias also delegating to `parent_agent_session_id` (the legacy alias chain `parent_chat_session_id -> parent_session_id -> parent_agent_session_id` continues to resolve transparently).
- `role` (DataField) -- session specialization ("pm", "dev", or null for legacy)
- `stage_states` -- derived property reading from `session_events`
- `slug` -- derives branch name, plan path, worktree
- `issue_url`, `plan_url`, `pr_url` -- SDLC link URLs

### Session Creation
Sessions are created via factory methods:
- `AgentSession.create_pm(...)` -- creates a PM session
- `AgentSession.create_teammate(...)` -- creates a Teammate session
- `AgentSession.create_dev(...)` -- creates a Dev session (wrapper for `create_child(role="dev")`)
- `AgentSession.create_child(role=..., ...)` -- generic child session creation

Or directly via `AgentSession.create(session_type="pm", ...)`.

### Derived Properties
- `is_pm`, `is_teammate`, `is_dev` -- type checks
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

The PM session owns all SDLC intelligence. The bridge just keeps it working.

### Completion Detection
1. **Rate limited** -> wait with backoff, then nudge
2. **Empty output** -> nudge (not deliver)
3. **end_turn + substantial output** -> deliver to Telegram
4. **Safety cap** -> deliver regardless (50 nudges for work sessions, 10 for Teammate sessions)
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
Human replies during active pipelines are buffered as steering messages on the PM session. The buffer is bounded at 10 messages (oldest dropped on overflow).

## Stage-by-Stage Orchestration

The PM session orchestrates SDLC work by spawning one Dev session per pipeline stage, rather than delegating the entire pipeline to a single Dev session.

### Flow

1. **PM assesses current stage** -- uses read-only Bash commands (gh, grep) to check what exists (issue, plan, PR, test status, review state)
2. **PM spawns one Dev session** -- dispatches a single-stage assignment with the Agent tool, including stage name, issue/PR URLs, current state, and acceptance criteria
3. **Dev session executes the assigned stage** -- runs the appropriate skill (/do-plan, /do-build, /do-test, etc.) and reports the result
4. **PM verifies the result** -- checks that the stage completed successfully
5. **PM repeats** -- assesses the next stage, spawns another Dev session, until the pipeline is complete or human input is needed

### Why Stage-by-Stage

- **Accountability**: Each stage result is verified before progressing
- **Visibility**: The PM can report intermediate progress to stakeholders
- **Recovery**: If a stage fails, the PM can re-dispatch or escalate without losing prior work
- **Judgment**: The PM decides whether trivial/docs-only work warrants the full pipeline

### Completion Warning

The stop hook (`.claude/hooks/stop.py`) includes a warning for SDLC-classified sessions that complete without any stage progress. This catches cases where the Dev session bypasses the pipeline. The warning is logged to stderr and is non-fatal.

## Hook-Driven Lifecycle

The parent-child session lifecycle is driven by two SDK hooks: **PreToolUse** and **SubagentStop**. These hooks automatically register child Dev sessions in Redis, start pipeline stages, and record stage outcomes when the child completes.

### Child Session Self-Registration (VALOR_PARENT_SESSION_ID)

Before spawning the child subprocess, `sdk_client.py._create_options()` injects the parent's `agent_session_id` UUID as `VALOR_PARENT_SESSION_ID` into the child's subprocess environment. This is how the child subprocess knows which parent to link to:

```python
# In sdk_client.py._create_options()
if self.agent_session_id and self.session_type in (SessionType.PM, SessionType.TEAMMATE):
    env["VALOR_PARENT_SESSION_ID"] = self.agent_session_id
```

When the child Claude Code CLI starts, `user_prompt_submit.py` fires on the first prompt. It reads `VALOR_PARENT_SESSION_ID` and passes it to `create_local()`:

```python
# In user_prompt_submit.py
parent_agent_session_id = os.environ.get("VALOR_PARENT_SESSION_ID")
agent_session = AgentSession.create_local(
    session_id=local_session_id,
    ...
    **({"parent_agent_session_id": parent_agent_session_id} if parent_agent_session_id else {}),
)
```

This creates **one** `local-*` AgentSession record with `parent_agent_session_id` correctly set to the parent's `agent_session_id` UUID.

**Key**: `VALOR_PARENT_SESSION_ID` carries the `agent_session_id` UUID (e.g., `agt_xxx`), not the bridge `session_id` (e.g., `tg_valor_...`). This is the canonical FK stored in `parent_agent_session_id` on the child's AgentSession record.

**Edge cases**:
- `VALOR_PARENT_SESSION_ID` absent (non-child sessions, Dev sessions): `os.environ.get()` returns `None`, `create_local()` behaves identically to before.
- `VALOR_PARENT_SESSION_ID` present but parent record deleted: child session saves fine, `parent_agent_session_id` points to a non-existent record (pre-existing risk, not introduced by this approach).

### Spawn-Execute-Return Flow

```
sdk_client._create_options() injects VALOR_PARENT_SESSION_ID=<agent_session_id>
    |
    v
PM (PM session) calls Agent tool with type="dev-session"
    |
    v
PreToolUse hook fires (agent/hooks/pre_tool_use.py)
    |-- Detects tool_name == "Agent", type == "dev-session"
    |-- session_registry.resolve(claude_uuid) -> bridge session_id
    |-- _extract_stage_from_prompt(prompt) -> e.g. "BUILD"
    |-- PipelineStateMachine(parent).start_stage("BUILD")
    |       -> marks stage as "in_progress" in parent's stage_states
    |   NOTE: create_child() is NOT called here (issue #808).
    |         The child subprocess self-registers (see below).
    |
    v
Child subprocess starts (Claude Code CLI)
    |
    v
user_prompt_submit.py fires on first prompt
    |-- Reads VALOR_PARENT_SESSION_ID from env -> parent agent_session_id UUID
    |-- AgentSession.create_local(session_id="local-*", parent_agent_session_id=...)
    |       -> Creates ONE linked AgentSession record (no orphaned dev-* record)
    |
    v
Dev session executes assigned work
    |-- Runs the appropriate skill (/do-build, /do-test, etc.)
    |-- Commits code, runs tests, produces output
    |
    v
SubagentStop hook fires (agent/hooks/subagent_stop.py)
    |-- Detects agent_type == "dev-session"
    |-- session_registry.resolve(claude_uuid) -> bridge session_id
    |-- Two-lookup: session_id -> AgentSession -> agent_session_id UUID
    |-- AgentSession.query.filter(parent_agent_session_id=agent_session_id_uuid)
    |       -> Finds local-* child record
    |-- Marks Dev session status = "completed" in Redis
    |-- _extract_output_tail(input_data) -> last ~500 chars
    |-- PipelineStateMachine(parent).classify_outcome(stage, ..., output_tail)
    |       |
    |       |-- "success" or "ambiguous" -> complete_stage(stage)
    |       |-- "fail" or "partial"      -> fail_stage(stage)
    |
    v
Hook returns {"reason": "Pipeline state: {stage_states}"}
    -> Injected into PM's context so it sees current pipeline state
```

### Key Components

| Component | File | Role |
|-----------|------|------|
| `_create_options()` | `agent/sdk_client.py` | Injects `VALOR_PARENT_SESSION_ID` into child subprocess env (PM/Teammate sessions only) |
| `pre_tool_use_hook()` | `agent/hooks/pre_tool_use.py` | Starts pipeline stage on dev-session Agent tool call (Skill path also handled) |
| `_maybe_start_pipeline_stage()` | `agent/hooks/pre_tool_use.py` | Starts the SDLC pipeline stage; does NOT create child AgentSession (child self-registers) |
| `post_tool_use_hook()` | `agent/hooks/post_tool_use.py` | Completes pipeline stage for Skill path; always runs watchdog health check |
| `subagent_stop_hook()` | `agent/hooks/subagent_stop.py` | Completes Dev session, classifies outcome, records stage result (dev-session path) |
| `_register_dev_session_completion()` | `agent/hooks/subagent_stop.py` | Two-lookup pattern: bridge session_id → agent_session_id UUID → find child local-* records |
| `session_registry` | `agent/hooks/session_registry.py` | Maps Claude Code UUIDs to bridge session IDs (see [Session Isolation](session-isolation.md)) |
| `PipelineStateMachine` | `bridge/pipeline_state.py` | Manages stage_states on the parent AgentSession |
| `_extract_stage_from_prompt()` | `agent/hooks/pre_tool_use.py` | Parses "Stage: BUILD" patterns from dev-session prompts |
| `classify_outcome()` | `bridge/pipeline_state.py` | Three-tier classification: OUTCOME contract, stop_reason, text patterns |
| `user_prompt_submit.py` | `.claude/hooks/user_prompt_submit.py` | On first prompt, creates the `local-*` AgentSession record; reads `SESSION_TYPE` and `VALOR_PARENT_SESSION_ID` env vars to register persona and parent linkage |

### Stage Extraction

The PreToolUse hook extracts the SDLC stage name from the dev-session prompt using pattern matching. It recognizes patterns like `Stage: BUILD`, `Stage to execute -- PLAN`, and falls back to scanning for standalone stage names near the "stage" keyword. Recognized stages: ISSUE, PLAN, CRITIQUE, BUILD, TEST, PATCH, REVIEW, DOCS, MERGE.

### Outcome Classification

When a Dev session completes, the SubagentStop hook extracts the last ~500 characters of output (from the agent transcript file or fallback summary) and passes them to `PipelineStateMachine.classify_outcome()`. Classification uses three tiers: Tier 0 parses structured `<!-- OUTCOME {...} -->` contracts emitted by skills, Tier 1 checks SDK stop_reason, and Tier 2 falls back to text pattern matching. The outcome determines whether the stage is marked as completed or failed on the parent session:

- **success** or **ambiguous** -> `complete_stage()` (safe default for ambiguous)
- **fail** or **partial** -> `fail_stage()`

### Error Handling

Both hooks wrap all operations in try/except blocks. Failures are logged as warnings but never raised -- the hooks must not crash the Agent tool or block the PM from continuing. If stage extraction fails (e.g., empty prompt), the hook skips the `start_stage()` call gracefully. If the session registry has no mapping (e.g., running outside the bridge), the hook skips Dev session registration entirely.

## Parent-Child Steering

The PM session can push steering messages to its running child Dev sessions, enabling mid-execution course correction without waiting for the Dev session to complete.

### Mechanism

The PM invokes `scripts/steer_child.py` via bash with the child's session ID and a steering message. The script validates the parent-child relationship (via `parent_session_id`) and pushes to the child's Redis steering queue. The child's watchdog hook picks up the message on the next tool call.

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

The PM persona uses different output formatting for Q&A sessions versus work sessions. The Teammate session type (`session_type="teammate"`) is the branch point for formatting differences.

### Teammate Mode (conversational prose)

When `session_type="teammate"`:
- **Instructions**: `build_teammate_instructions()` in `agent/teammate_handler.py` emphasizes research-first behavior -- search code, query memory, consult docs, cite findings
- **Summarizer**: The summarizer LLM receives teammate context and produces conversational prose instead of bullets
- **Structured summary bypass**: `_compose_structured_summary()` in `bridge/summarizer.py` returns the LLM summary directly without emoji prefix, bullet parsing, or structured template
- **Reaction**: Processing reaction is cleared (set to `None`) after delivery instead of setting a completion emoji
- **Single delivery path**: Teammate always goes through the summarizer -- no dual-path ambiguity with `send_telegram.py`

### Work Mode (structured formatting)

For PM and Dev sessions:
- **Summarizer**: Produces bullet points with status emoji prefix
- **Structured summary**: Full formatting with emoji, stage line (for SDLC), bullets, question section, and link footer
- **Reaction**: Completion emoji set on success

### Data Flow

```
Teammate message -> session_type="teammate" on AgentSession
    -> Agent researches (code, memory, docs)
    -> Agent returns prose answer
    -> Summarizer formats as prose (teammate context)
    -> _compose_structured_summary() bypasses structured template
    -> Telegram delivers prose directly
    -> Processing reaction cleared (None)
```

## Agent Definitions

The `dev-session` agent is defined in `agent/agent_definitions.py`:
- `tools=None` (all tools, full write permissions)
- `model=None` (inherits from parent session)
- Single-stage executor: receives a stage assignment from the PM, executes it, reports result
- Spawned by PM session via the Agent tool

## Key Files

| File | Purpose |
|------|---------|
| `models/agent_session.py` | AgentSession model with session_type discriminator |
| `agent/agent_definitions.py` | Agent registry including dev-session |
| `agent/agent_session_queue.py` | Queue with nudge loop and per-chat workers; reads `session.project_config` at execution time; zero module-level bridge imports |
| `agent/output_handler.py` | `OutputHandler` protocol for routing agent output; `FileOutputHandler` (logs to `logs/worker/`) and `LoggingOutputHandler` implementations |
| `agent/constants.py` | Canonical location for `REACTION_SUCCESS/COMPLETE/ERROR` (re-exported from `bridge/response.py`) |
| `agent/session_logs.py` | Canonical location for `save_session_snapshot()` (re-exported from `bridge/session_logs.py`) |
| `agent/sdk_client.py` | SDK client; uses `project_key` identity checks for cross-repo detection |
| `worker/__main__.py` | Standalone worker entry point (`python -m worker`); processes sessions without Telegram bridge |

## Project Config Propagation

When a Telegram message arrives, `find_project_for_chat()` resolves the full project config from `projects.json` once. This config is passed through `enqueue_agent_session(project_config=config)` and stored on the `AgentSession.project_config` DictField. At execution time, `_execute_agent_session()` reads the config directly from the session -- no parallel registry or re-derivation needed.

```
Telegram message
    -> find_project_for_chat() resolves full project dict
    -> enqueue_agent_session(project_config=project_dict)
    -> AgentSession.project_config stores the dict in Redis
    -> _execute_agent_session() reads session.project_config
    -> get_agent_response_sdk() receives project dict with all fields
```

**Cross-repo detection**: `sdk_client.py` uses `project_key != "valor"` to determine whether a session targets a cross-repo project, replacing the previous `project_working_dir != AI_REPO_ROOT` string comparisons.

**Backward compatibility**: Legacy sessions without `project_config` (created before this field existed) fall back to loading from `projects.json` at execution time. This transitional fallback can be removed after one deploy cycle.

**Config consumers**: `bridge/formatting.py` and `tools/agent_session_scheduler.py` load config from `projects.json` directly via `bridge.routing.load_config()` rather than relying on a module-level registry.

## Migration

- Older AgentSession records in Redis with `session_type="chat"` need migration via `scripts/migrate_session_type_chat_to_pm.py`
- Float timestamps are auto-converted to datetime via `__setattr__`; run `scripts/migrate_datetime_fields.py` for existing data
- `_normalize_kwargs()` maps deprecated field names to consolidated equivalents
- Workers auto-adapt: jobs with chat_id use per-chat routing; older jobs fall back to project_key
