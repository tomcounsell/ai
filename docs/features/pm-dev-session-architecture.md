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

## Enforcement — PM Session Tool Restrictions

PM sessions are **read-only by design**. Enforcement lives in the SDK-level hook
at [`agent/hooks/pre_tool_use.py`](../../agent/hooks/pre_tool_use.py) (registered
via `claude_agent_sdk.HookMatcher` in `agent/hooks/__init__.py`). The hook runs
before every tool call and returns `{"decision": "block", "reason": ...}` when a
PM session attempts a mutating operation. Three layers of enforcement apply:

1. **Write/Edit blocklist.** The hook blocks any `Write` or `Edit` tool call to
   a path outside `docs/` when `SESSION_TYPE=pm`. This means the PM can edit
   plan documents, design docs, and feature docs but cannot touch source code,
   tests, configs, or the worktree. See `_is_pm_allowed_write` in the hook.

2. **Bash read-only allowlist.** The `Bash` branch of the hook restricts PM
   commands to an explicit prefix allowlist (`git status`, `git log`, `git
   diff`, `gh issue view`, `gh pr view`, `gh pr list`, `tail logs/`,
   `cat docs/`, `python -m tools.valor_session status`, etc.). Any command not
   on the list -- or any command containing shell metacharacters (`|`, `>`,
   `&&`, `;`, `` ` ``, `$(`, `&`) that could smuggle a mutation past the
   prefix check -- is rejected. `git -C <path>` is normalized to bare `git`
   before matching so cross-repo forms like `git -C "$REPO" status` work.
   See `_is_pm_allowed_bash` and `PM_BASH_ALLOWED_PREFIXES` in the hook.
   `gh api` is deliberately excluded because `--method POST/PATCH/DELETE`
   would pass a naive prefix check.

3. **Anomaly-response rule (persona-level).** Even with the hook in place, the
   PM persona prompt at [`config/personas/project-manager.md`](../../config/personas/project-manager.md)
   includes an "Anomaly Response — Hibernate, Do Not Self-Heal" rule instructing
   the PM to surface broken-workspace errors to the human rather than attempting
   recovery. This is belt-and-suspenders alongside the tool-layer enforcement:
   the hook prevents destructive commands from running; the persona rule keeps
   the PM from trying in the first place.

Any mutation (building, testing, committing, installing, recovering) must be
dispatched to a Dev session via `python -m tools.valor_session create --role dev --parent "$AGENT_SESSION_ID" --message "..."`. The worker creates and routes the Dev session with full tool access. The PM's hook allowlist only blocks PM sessions (`SESSION_TYPE=pm`), so Dev sessions retain full tool access.

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
        Intent Classifier (Haiku, four-way)
            |
            |-- Collaboration/Other --> Direct-action mode (PM handles with tools)
            |                               |
            |                               v
            |                           Telegram Response
            |
            |-- Work (or low confidence)
                    |-- Stage-by-stage SDLC orchestration
                    |
                    v
                PM assesses current stage
                    |-- Creates Dev session via valor_session CLI
                    |-- Worker executes Dev session (SDK or CLI harness)
                    |-- Worker steers PM with completion status
                    |-- PM verifies and repeats until pipeline complete
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
- `project_config` (DictField) -- full project dict from `projects.json`, populated at enqueue time. Carries all project properties (name, working_directory, github, mode, telegram, etc.) through the pipeline so downstream code never re-derives from a parallel registry. Empty/None for older sessions created before this field existed; the worker falls back to loading from `projects.json` at execution time.

### PM/Teammate session-specific fields
- `chat_id`, `message_id`, `sender_name`, `message_text` -- Telegram context
- `result_text` -- what was delivered to Telegram

### Dev session-specific fields
- `parent_agent_session_id` (KeyField) -- **canonical** parent link (role-neutral). Set by all session creators (`create_child`, `create_dev`, `enqueue_session`) and read by all hierarchy walkers (`scheduling_depth`, `get_parent_session`, `get_child_sessions`, the zombie health check, the dashboard).
- `parent_session_id` -- backward-compat `@property` alias delegating to `parent_agent_session_id`. Kept for one release cycle. New code should use `parent_agent_session_id` directly.
- `parent_chat_session_id` -- backward-compat `@property` alias also delegating to `parent_agent_session_id` (the alias chain `parent_chat_session_id -> parent_session_id -> parent_agent_session_id` continues to resolve transparently).
- `role` (DataField) -- session specialization ("pm", "dev", or null for unspecialized sessions)
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

Workers are keyed by `worker_key` — either `project_key` (for PM and dev-without-slug sessions that share the main working tree) or `chat_id` (for teammate and slugged-dev sessions with isolated worktrees). Sessions sharing a working tree serialize; isolated sessions can run in parallel.

### Per-Worker-Key Workers
- `_ensure_worker(worker_key, is_project_keyed)` -- starts a worker per key
- `_worker_loop(worker_key, event, is_project_keyed)` -- processes sessions for a key
- `_pop_agent_session(worker_key, is_project_keyed)` -- pops by worker_key
- Callbacks remain per `project_key` (Telegram client is project-scoped)

### Steering Messages
Human replies during active pipelines are buffered as steering messages on the PM session. The buffer is bounded at 10 messages (oldest dropped on overflow).

## Stage-by-Stage Orchestration

The PM session orchestrates SDLC work by spawning one Dev session per pipeline stage, rather than delegating the entire pipeline to a single Dev session.

### Flow

1. **PM assesses current stage** -- uses read-only Bash commands (gh, grep) to check what exists (issue, plan, PR, test status, review state)
2. **PM creates one Dev session** -- calls `python -m tools.valor_session create --role dev --parent "$AGENT_SESSION_ID" --message "..."` with the stage assignment (stage name, issue/PR URLs, current state, acceptance criteria)
3. **Worker executes the Dev session** -- routes to SDK or CLI harness based on `DEV_SESSION_HARNESS`; runs the appropriate skill (/do-plan, /do-build, /do-test, etc.)
4. **Worker steers PM with result** -- `_handle_dev_session_completion()` classifies outcome, updates PipelineStateMachine, posts GitHub stage comment, and steers the parent PM session
5. **PM verifies the result** -- receives steering message with completion status and stage outcome
6. **PM repeats** -- assesses the next stage, creates another Dev session, until the pipeline is complete or human input is needed

### Why Stage-by-Stage

- **Accountability**: Each stage result is verified before progressing
- **Visibility**: The PM can report intermediate progress to stakeholders
- **Recovery**: If a stage fails, the PM can re-dispatch or escalate without losing prior work
- **Judgment**: The PM decides whether trivial/docs-only work warrants the full pipeline

### Completion Warning

The stop hook (`.claude/hooks/stop.py`) includes a warning for SDLC-classified sessions that complete without any stage progress. This catches cases where the Dev session bypasses the pipeline. The warning is logged to stderr and is non-fatal.

## Worker-Driven Lifecycle

The parent-child session lifecycle is driven by the worker's post-completion handler and two SDK hooks for stage tracking.

### Dev Session Creation (PM → valor_session CLI)

The PM session creates Dev sessions by calling:

```bash
python -m tools.valor_session create --role dev --parent "$AGENT_SESSION_ID" --message "Stage: BUILD\n..."
```

This enqueues a new `AgentSession` record with `session_type="dev"` and `parent_agent_session_id` set to the PM's `agent_session_id`. The worker then picks up and executes the session.

### Spawn-Execute-Return Flow

```
PM session calls valor_session create --role dev --parent <agent_session_id>
    |
    v
AgentSession created (session_type="dev", parent_agent_session_id=<pm_id>)
    |
    v
Worker picks up Dev session from Redis queue
    |
    v
_execute_agent_session() routes by DEV_SESSION_HARNESS
    |-- sdk (default): get_agent_response_sdk()
    |-- claude-cli: get_response_via_harness()  <- CLI subprocess
    |
    v
Dev session executes assigned work
    |-- Runs the appropriate skill (/do-build, /do-test, etc.)
    |-- Commits code, runs tests, streams output
    |
    v
_handle_dev_session_completion() (agent/agent_session_queue.py)
    |-- Looks up parent PM session via parent_agent_session_id
    |-- PipelineStateMachine(parent).classify_outcome(stage, result)
    |       |
    |       |-- "success" or "ambiguous" -> complete_stage(stage)
    |       |-- "fail" or "partial"      -> fail_stage(stage)
    |-- post_stage_comment() -> GitHub issue comment
    |-- steer_session(parent.session_id, completion_summary)
    |       -> PM receives steering message with stage outcome
```

### SDK Hook Path (PM Sessions Using Skill Tool)

When a PM session invokes a Skill directly (e.g., `Skill(skill="do-build")`), the pre/post hooks track stage transitions:

- **PreToolUse** (`agent/hooks/pre_tool_use.py`): Detects Skill tool, looks up stage in `_SKILL_TO_STAGE`, calls `PipelineStateMachine(parent).start_stage()`. Session ID from `AGENT_SESSION_ID` env var.
- **PostToolUse** (`agent/hooks/post_tool_use.py`): Detects Skill completion, calls `_complete_pipeline_stage()`. Reads current in_progress stage from Redis directly.

### Key Components

| Component | File | Role |
|-----------|------|------|
| `_handle_dev_session_completion()` | `agent/agent_session_queue.py` | Worker post-completion: classifies outcome, posts GitHub comment, steers parent PM |
| `_extract_issue_number()` | `agent/agent_session_queue.py` | Resolves tracking issue from env vars or session message_text |
| `pre_tool_use_hook()` | `agent/hooks/pre_tool_use.py` | Starts pipeline stage on Skill tool calls (PM Skill path) |
| `post_tool_use_hook()` | `agent/hooks/post_tool_use.py` | Completes pipeline stage for Skill path |
| `subagent_stop_hook()` | `agent/hooks/subagent_stop.py` | Logs Dev session completion (SDLC tracking moved to worker) |
| `PipelineStateMachine` | `agent/pipeline_state.py` | Manages stage_states on the parent AgentSession (moved from `bridge/` in Phase 3) |
| `classify_outcome()` | `agent/pipeline_state.py` | Three-tier classification: OUTCOME contract, stop_reason, text patterns |
| `get_definition()` | `agent/agent_definitions.py` | Returns actionable error for stale callers requesting `"dev-session"` Agent tool dispatch |
| `user_prompt_submit.py` | `.claude/hooks/user_prompt_submit.py` | On first prompt, creates the `local-*` AgentSession record; reads `SESSION_TYPE` and `VALOR_PARENT_SESSION_ID` env vars |

### Outcome Classification

After the worker's harness completes, `_handle_dev_session_completion()` passes the result text to `PipelineStateMachine.classify_outcome()`. Classification uses three tiers: Tier 0 parses structured `<!-- OUTCOME {...} -->` contracts emitted by skills, Tier 1 checks SDK stop_reason, and Tier 2 falls back to text pattern matching. The outcome determines whether the stage is marked completed or failed on the parent session:

- **success** or **ambiguous** -> `complete_stage()` (safe default for ambiguous)
- **fail** or **partial** -> `fail_stage()`

### Error Handling

`_handle_dev_session_completion()` wraps all operations in try/except. Failures are logged as warnings but never raised — the worker must not crash on completion handling failures. The PM session still receives the steering message even if GitHub comment posting fails.

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

The `dev-session` Agent tool entry has been removed from `agent/agent_definitions.py` (Phase 5 cleanup). Dev sessions are now created as `AgentSession` records via `python -m tools.valor_session create --role dev`, not via the Agent tool. `get_definition()` returns an actionable error if a stale PM persona still calls `Agent(subagent_type="dev-session")`.

## Key Files

| File | Purpose |
|------|---------|
| `models/agent_session.py` | AgentSession model with session_type discriminator |
| `agent/agent_definitions.py` | Agent registry; `get_definition()` provides actionable error for stale dev-session callers |
| `agent/agent_session_queue.py` | Queue with nudge loop and per-worker-key serialization; `_handle_dev_session_completion()` for post-harness SDLC lifecycle |
| `agent/output_handler.py` | `OutputHandler` protocol for routing agent output; `TelegramRelayOutputHandler` (Redis outbox for Telegram delivery), `FileOutputHandler` (logs to `logs/worker/`), and `LoggingOutputHandler` implementations |
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

**Backward compatibility**: Older sessions without `project_config` (created before this field existed) fall back to loading from `projects.json` at execution time. This transitional fallback can be removed after one deploy cycle.

**Config consumers**: `bridge/formatting.py` and `tools/agent_session_scheduler.py` load config from `projects.json` directly via `bridge.routing.load_config()` rather than relying on a module-level registry.

## Migration

- Older AgentSession records in Redis with `session_type="chat"` need migration via `scripts/migrate_session_type_chat_to_pm.py`
- Float timestamps are auto-converted to datetime via `__setattr__`; run `scripts/migrate_datetime_fields.py` for existing data
- `_normalize_kwargs()` maps old field names to consolidated equivalents
- Workers auto-adapt: jobs with chat_id use per-chat routing; older jobs fall back to project_key
