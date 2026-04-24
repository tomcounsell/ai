# PM/Teammate/Dev session Architecture

## Overview

The AgentSession model uses a **session_type discriminator** (`SessionType` enum from `config/enums.py`) to distinguish between three session roles:

- **PM Session** (`session_type=SessionType.PM`): Read-only CLI harness session with PM persona. Owns the Telegram conversation, orchestrates work, and spawns Dev sessions. For multi-issue requests, a parent PM session can also spawn child PM sessions (see [PM Session Child Fan-out](pm-session-child-fanout.md)).
- **Teammate Session** (`session_type=SessionType.TEAMMATE`): Conversational CLI harness session with Teammate persona. Handles informational queries directly without spawning Dev sessions.
- **Dev session** (`session_type=SessionType.DEV`): Full-permission CLI harness session with Dev persona. Executes a single assigned SDLC stage and reports the result back to the PM.

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

There are exactly three session types: `pm`, `teammate`, and `dev`. `session_type` is the **sole discriminator** for routing, permission injection, summarizer formatting, and nudge cap selection. The `session_mode` field on `AgentSession` remains as a no-op `Field(null=True)` purely to keep Redis deserialization safe for in-flight records during the 30-day TTL window; it has zero readers and zero writers in application code. See [Config-Driven Chat Mode](config-driven-chat-mode.md) for the config schema and resolution order.

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
                    |-- Worker executes Dev session (CLI harness)
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
- `continuation_depth` (IntField, default 0) -- tracks how many continuation PM sessions have been chained from the original. Capped at `_CONTINUATION_PM_MAX_DEPTH` (3) to prevent runaway chains.
- `project_key`, `created_at`, `history`, etc.
- `project_config` (DictField) -- full project dict from `projects.json`, populated at enqueue time. Carries all project properties (name, working_directory, github, mode, telegram, etc.) through the pipeline so downstream code never re-derives from a parallel registry. Empty/None for older sessions created before this field existed; the worker falls back to loading from `projects.json` at execution time.

### PM/Teammate session-specific fields
- `chat_id`, `message_id`, `sender_name`, `message_text` -- Telegram context
- `result_text` -- what was delivered to Telegram

### Dev session-specific fields
- `parent_agent_session_id` (KeyField) -- **canonical** parent link (role-neutral). Set by all session creators (`create_child`, `create_dev`, `enqueue_session`) and read by all hierarchy walkers (`scheduling_depth`, `get_parent_session`, `get_child_sessions`, the zombie health check, the dashboard).
- `role` (DataField) -- session specialization ("pm", "dev", or null for unspecialized sessions)
- `stage_states` -- derived property reading from `session_events`
- `slug` -- derives branch name, plan path, worktree
- `issue_url`, `plan_url`, `pr_url` -- SDLC link URLs

**Worker-restart persistence:** Local dev sessions (those with `session_id` starting `local`) persist across worker restarts; PM/teammate local sessions do not (issue #1092). Dev sessions are worker-owned — the PM spawned them via `valor-session create --role dev`, and no human CLI is holding the same `claude_session_uuid`, so startup recovery re-queues them to `pending` like bridge sessions. See the "Local session recovery is `session_type`-aware" section in [`bridge-worker-architecture.md`](bridge-worker-architecture.md) for the full rationale.

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

## Deadlock Prevention

When a PM session dispatches a child dev session and enters `waiting_for_children` status, three mechanisms prevent the PM from starving the child of worker slots (issue #1004):

1. **Output router guard**: `determine_delivery_action()` in `agent/output_router.py` checks `session_status == "waiting_for_children"` *before* the PM+SDLC nudge check. When the guard fires, the PM's output is delivered (not nudged), allowing the session to exit cleanly and release its global semaphore slot. Without this guard, the nudge loop would re-enqueue the PM as `pending`, consuming a slot on every cycle while the child sits in the queue.

2. **Child priority boost**: `_pop_agent_session()` in `agent/agent_session_queue.py` boosts child sessions whose parent is in `waiting_for_children` status. Within the same priority tier, these children sort before parentless sessions (FIFO is preserved among equals). This ensures the child gets the next available slot rather than competing with unrelated sessions.

3. **Immediate PM re-enqueue**: `_handle_dev_session_completion()` transitions the parent PM from `waiting_for_children` to `pending` immediately after steering succeeds, rather than waiting for the periodic hierarchy health check. The health check remains as a safety-net fallback.

4. **Session status re-read**: `send_to_chat()` re-reads the agent session from Redis before the routing decision. The in-memory copy is loaded with `status="running"` at session start and becomes stale when the PM calls `wait-for-children` (which updates Redis directly). Without this re-read, the output router guard would never fire.

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

Workers are keyed by `worker_key` — either `project_key` (for PM and dev-without-slug sessions that share the main working tree), `slug` (for slugged dev sessions with isolated worktrees), or `chat_id` (for teammate sessions). Sessions sharing a working tree serialize; isolated sessions can run in parallel across chats and across slugs.

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
2. **PM creates one Dev session** -- calls `python -m tools.valor_session create --role dev --model <opus|sonnet> --parent "$AGENT_SESSION_ID" --message "..."` with a structured briefing. See the "Dispatch Message Format" section in `config/personas/project-manager.md` for the six required fields (Problem Summary, Key Files, Prior Stage Findings, Constraints, Current State, Acceptance Criteria) and per-stage model selection.
3. **Worker executes the Dev session** -- routes to CLI harness (`claude -p`); runs the appropriate skill (/do-plan, /do-build, /do-test, etc.)
4. **Worker steers PM with result** -- `_handle_dev_session_completion()` classifies outcome, updates PipelineStateMachine, posts GitHub stage comment, and steers the parent PM session
5. **PM verifies the result** -- receives steering message with completion status and stage outcome
6. **PM repeats** -- assesses the next stage, creates another Dev session, until the pipeline is complete or human input is needed

### Why Stage-by-Stage

- **Accountability**: Each stage result is verified before progressing
- **Visibility**: The PM surfaces questions and the final delivery summary to the stakeholder, not intermediate step-by-step narration
- **Recovery**: If a stage fails, the PM can re-dispatch or escalate without losing prior work
- **No bypass**: All software changes route through the full pipeline — triviality is not an override. Docs-only work (no code, no PR) may skip BUILD/TEST/REVIEW but still requires an issue and DOCS stage.

### Spawn vs. Resume

When directing a Dev session, the PM chooses between spawning a fresh session or resuming a recently completed one:

| Situation | Action |
|-----------|--------|
| Dev session recently completed, same issue, context still warm (branch checked out, transcript loaded) | Resume: `python -m tools.valor_session resume --id <id> --message "..."` |
| New issue, different codebase area | Spawn fresh: `python -m tools.valor_session create --role dev ...` |
| Prior session's context would be stale or misleading | Spawn fresh |
| Parallel work needed on separate issues | Spawn fresh for each |

Resuming avoids the cold-start cost of re-loading codebase context and keeps the in-progress branch checked out. Spawning fresh is safer when context from the prior session could lead the agent astray.

### PM Session Lifecycle: Wait and Continuation Fallback

When a PM session dispatches a Dev session, the PM must stay alive to receive the Dev session's completion steering message. Two mechanisms ensure pipeline progression:

**1. Wait-for-children (optimization path)**

After dispatching any Dev session, the PM persona is instructed to:
1. Call `python -m tools.valor_session wait-for-children --session-id "$AGENT_SESSION_ID"` to signal waiting status.
2. Output a brief status message.
3. Wait for the steering response without producing a final answer.

This keeps the SDK turn loop alive so `_handle_dev_session_completion()` can steer the PM directly. Note: `wait-for-children` returns immediately (it transitions the session status but does not block). The PM stays alive only because the SDK turn loop has not ended.

**2. Continuation PM (guaranteed fallback)**

If the PM exits before the Dev session completes (the common case when the LLM ends its turn loop), `_handle_dev_session_completion()` detects this via the `steer_session()` return value. **Ordering invariant (issue #987)**: `_handle_dev_session_completion()` is always called *after* `complete_transcript()`, which synchronously runs `_finalize_parent_sync()` to completion. This means the re-check guard reads the PM's post-finalization status — avoiding the race where a steer was accepted but the PM then finalized before consuming it.

- **Steer succeeds + parent still active at re-check**: Direct delivery (optimal path).
- **Steer succeeds + parent terminal at re-check** (post-`_finalize_parent_sync` state): Creates a continuation PM immediately — the steering message is orphaned and the PM will never consume it.
- **Steer fails** (parent already terminal): Creates a continuation PM.
- **Path B (`agent_session=None`)**: When the `status="running"` filter returned `None` (race with health-check recovery), falls back to `session.parent_agent_session_id` to look up the parent and create a continuation PM — no silent skip.

The continuation PM is a new `AgentSession(session_type="pm", status="pending")` containing:
- The issue number, completed stage, outcome, and result preview.
- `parent_agent_session_id` set to the original PM for lineage tracking.
- `continuation_depth` incremented from the parent's value (capped at 3 to prevent runaway chains).

**Deduplication**: Redis `SETNX` on key `continuation-pm:{parent_id}` (300s TTL) ensures only one continuation PM per parent, even when multiple Dev sessions complete simultaneously.

**Monitoring**: The `[continuation-pm-created]` structured log tag is searchable by `scripts/reflections.py`. Daily metrics are tracked at `metrics:continuation_pm_created:{date}`.

**3. Transcript-boundary skip (issue #1156)**

`complete_transcript` and the Claude Code Stop hook are both **no-ops** (status-wise) for PMs currently in `waiting_for_children` when the target is `completed`/`failed`. Without this skip, the PM's own transcript ending would force-finalize the PM via `finalize_session`, bypassing the child-liveness gate inside `_finalize_parent_sync` and stranding children.

With the skip in place:
- The `SESSION_END` transcript marker is still written (preserved for auditability).
- The PM stays in `waiting_for_children` until one of the sanctioned channels finalizes it: `_finalize_parent_sync` after all children terminate, the completion runner after delivering the final summary, or — for genuinely wedged sessions — `_complete_agent_session`, health check, or watchdog recovery.

See `docs/features/session-lifecycle.md#transcript-boundary-skip-issue-1156` for the full rationale and caller audit.

### Single-Issue Scoping

PM sessions are scoped to a single issue when the incoming message references a specific issue number. The PM persona includes a hard rule (Rule 3) prohibiting `gh issue list` queries for other issues and dispatching stages for unrelated issues. This prevents cross-contamination between concurrent SDLC pipelines observed in production when one PM session assessed global state and dispatched BUILD for another PM's issue.

### Completion Warning

The stop hook (`.claude/hooks/stop.py`) includes a warning for SDLC-classified sessions that complete without any stage progress. This catches cases where the Dev session bypasses the pipeline. The warning is logged to stderr and is non-fatal.

### Per-Stage Model Selection

PM picks the Claude model at dispatch time using `valor-session create --model <name>`. PM's decision rules (see [pm-sdlc-decision-rules.md](pm-sdlc-decision-rules.md)) encode which stage gets which model. The full mapping is documented in [pipeline-graph.md](pipeline-graph.md) under "Per-Stage Model Selection."

### Dev Session Resume (Hard PATCH Path)

Normal PATCH dispatch spawns a fresh Dev session that reads the review findings or test failures from their artifacts and applies a targeted fix. When PATCH failures become non-trivial — architectural, cross-cutting, or tangled with implementation assumptions only the original builder knew — PM resumes the BUILD session's Claude Code transcript instead of starting fresh. The resumed session inherits the builder's full context for free.

#### Mechanism

1. BUILD Dev sessions set `retain_for_resume=True` on completion (harness guard in `_handle_dev_session_completion()`).
2. PM calls `valor-session resume --id <build-session-id> --message "Apply review findings: ..."`.
3. `cmd_resume` transitions the completed session back to `pending` (with `reject_from_terminal=False`) and appends the message to the steering queue.
4. Worker picks up the session; `sdk_client._create_options()` sees the stored `claude_session_uuid` and sets `resume=<uuid>` + `continue_conversation=True`.
5. After PR merge, PM calls `valor-session release --pr <N>` to clear `retain_for_resume`.

See [pm-sdlc-decision-rules.md](pm-sdlc-decision-rules.md) for when PM chooses resume vs fresh.

#### Resuming Killed or Failed Sessions (issue #1061)

`valor-session resume` also accepts sessions with status `killed` (operator-initiated kill) or `failed` (worker crash), not just `completed`. The gating condition is the presence of a stored `claude_session_uuid` — without a transcript UUID there is nothing to replay, and the CLI exits 1 with `"cannot resume: no transcript UUID stored (session was killed before first turn completed)"`.

`sdk_client._get_prior_session_uuid()` includes `killed` and `failed` in its status filter so the worker can retrieve the stored UUID when processing the resumed session. The primary cross-wire defense (#374) is unchanged: the lookup is still keyed on `session_id` and sorted by `created_at` desc, so only this thread's newest record is considered.

All five `valor-session` subcommands that take `--id` (`status`, `inspect`, `resume`, `steer`, `kill`) accept either `session_id` (the canonical routing key) or `agent_session_id` (the 32-char hex UUID the Claude Code CLI displays as "Session ID"). Resolution is `session_id` first with a fallback to `AgentSession.get_by_id()` — see `tools/valor_session._find_session()`.

**Rollback order** if a regression is traced to this change: revert the one-line status-filter expansion in `agent/sdk_client.py::_get_prior_session_uuid` first. `_find_session` is additive — it only alters behavior on previously-failing UUID lookups — and can remain in place.

## Worker-Driven Lifecycle

The parent-child session lifecycle is driven by the worker's post-completion handler and two SDK hooks for stage tracking.

### Dev Session Creation (PM → valor_session CLI)

The PM session creates Dev sessions by calling:

```bash
python -m tools.valor_session create --role dev --model <opus|sonnet> --parent "$AGENT_SESSION_ID" --message "Stage: BUILD\n..."
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
_execute_agent_session() routes all session types to CLI harness
    |-- _get_prior_session_uuid(session_id)  <- Popoto lookup (#976)
    |-- build_harness_turn_input(..., skip_prefix=<bool>)  <- two shapes
    |-- get_response_via_harness()  <- claude -p [--resume <uuid>] subprocess
    |       (see docs/features/harness-session-continuity.md)
    |
    v
Dev session executes assigned work
    |-- Runs the appropriate skill (/do-build, /do-test, etc.)
    |-- Commits code, runs tests, streams output
    |
    v
_handle_dev_session_completion() (agent/session_completion.py)
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
| `_handle_dev_session_completion()` | `agent/session_completion.py` | Worker post-completion: classifies outcome, posts GitHub comment, steers parent PM; creates continuation PM on steer failure |
| `_create_continuation_pm()` | `agent/session_completion.py` | Creates a continuation PM session when the parent PM is terminal — includes SETNX dedup, depth cap, and structured logging |
| `_extract_issue_number()` | `agent/session_completion.py` | Resolves tracking issue from env vars or session message_text |
| `pre_tool_use_hook()` | `agent/hooks/pre_tool_use.py` | Starts pipeline stage on Skill tool calls (PM Skill path) |
| `post_tool_use_hook()` | `agent/hooks/post_tool_use.py` | Completes pipeline stage for Skill path |
| `PipelineStateMachine` | `agent/pipeline_state.py` | Manages stage_states on the parent AgentSession (moved from `bridge/` in Phase 3) |
| `classify_outcome()` | `agent/pipeline_state.py` | Three-tier classification: OUTCOME contract, stop_reason, text patterns |
| `get_definition()` | `agent/agent_definitions.py` | Returns actionable error for stale callers requesting `"dev-session"` Agent tool dispatch |
| `user_prompt_submit.py` | `.claude/hooks/user_prompt_submit.py` | On first prompt, decides attach-vs-create: worker-spawned subprocesses attach the sidecar to the worker's existing AgentSession via `AGENT_SESSION_ID` / `VALOR_SESSION_ID` env vars (no new record, issue #1157); direct-CLI subprocesses fall through to `create_local()` gated by `SESSION_TYPE` / `VALOR_PARENT_SESSION_ID` |

### Outcome Classification

After the worker's harness completes, `_handle_dev_session_completion()` passes the result text to `PipelineStateMachine.classify_outcome()`. Classification uses three tiers: Tier 0 parses structured `<!-- OUTCOME {...} -->` contracts emitted by skills, Tier 1 checks SDK stop_reason, and Tier 2 falls back to text pattern matching. The outcome determines whether the stage is marked completed or failed on the parent session:

- **success** or **ambiguous** -> `complete_stage()` (safe default for ambiguous)
- **fail** or **partial** -> `fail_stage()`

### Error Handling

`_handle_dev_session_completion()` wraps all operations in try/except. Failures are logged as warnings but never raised — the worker must not crash on completion handling failures. The PM session still receives the steering message even if GitHub comment posting fails.

## Parent-Child Steering

The PM session can push steering messages to its running child Dev sessions, enabling mid-execution course correction without waiting for the Dev session to complete.

### Mechanism

The PM invokes `scripts/steer_child.py` via bash with the child's session ID and a steering message. The script validates the parent-child relationship (via `parent_agent_session_id`) and writes to the child's turn-boundary inbox (`AgentSession.queued_steering_messages`). The worker delivers the message at the next turn boundary.

**Delivery paths by harness type:**
- **CLI-harness sessions** (default): `steer_child.py` calls `steer_session()` which writes to `queued_steering_messages`. The worker injects the message as user input at the next turn boundary. There is no mid-turn injection — the Dev session sees the message at most one turn late.
- **Abort signals** (`--abort`): always use the Redis list (`steering:{session_id}`) regardless of harness type. The watchdog hook delivers these immediately via `additionalContext` injection.
- **SDK-harness sessions** (historical): both the turn-boundary inbox and the watchdog hook's mid-turn injection path are available, but all Dev sessions now default to CLI harness.

> **Status (2026-04-17):** The mid-execution hook consumer is not yet implemented. Messages written via `steer_child.py` are only consumed at session pickup and completion — they are silently dropped if the child session is already running. See issue TBD for consolidation plans.

```bash
# Steer a running child
python scripts/steer_child.py --session-id <child_id> --message "focus on tests" --parent-id <parent_id>

# Abort a child
python scripts/steer_child.py --session-id <child_id> --message "stop" --parent-id <parent_id> --abort

# List active children
python scripts/steer_child.py --list --parent-id <parent_id>
```

See [Session Steering](session-steering.md) for the turn-boundary inbox architecture and [Steering Queue: Historical Spec](steering-implementation-spec.md) for the Redis list / mid-turn injection path.

## Q&A Formatting (Prose vs Structured)

The PM persona uses different output formatting for Q&A sessions versus work sessions. The Teammate session type (`session_type="teammate"`) is the branch point for formatting differences.

### Teammate Mode (conversational prose)

When `session_type="teammate"`:
- **Instructions**: `build_teammate_instructions()` in `agent/teammate_handler.py` emphasizes research-first behavior -- search code, query memory, consult docs, cite findings
- **Drafter**: The drafter LLM receives teammate context and produces conversational prose instead of bullets
- **Structured draft bypass**: `_compose_structured_draft()` in `bridge/message_drafter.py` returns the LLM draft directly without emoji prefix, bullet parsing, or structured template
- **Reaction**: Processing reaction is cleared (set to `None`) after delivery instead of setting a completion emoji
- **Single delivery path**: Teammate always goes through the message drafter -- no dual-path ambiguity with `send_telegram.py`

### Work Mode (structured formatting)

For PM and Dev sessions:
- **Drafter**: Produces bullet points with status emoji prefix
- **Structured draft**: Full formatting with emoji, stage line (for SDLC), bullets, question section, and link footer
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
| `agent/agent_session_queue.py` | Queue dispatch surface — entry points (`enqueue_agent_session`, `register_callbacks`, worker loops); re-exports symbols from split modules |
| `agent/session_completion.py` | Post-execution lifecycle: `_handle_dev_session_completion()`, `_create_continuation_pm()`, finalization |
| `agent/session_executor.py` | Core execute loop: `_execute_agent_session()`, turn-boundary steering, nudge/re-enqueue |
| `agent/session_health.py` | Health monitor, startup recovery, orphan cleanup |
| `agent/session_pickup.py` | Pop locking, steering drain, session selection |
| `agent/session_state.py` | Shared globals: `_active_sessions`, `_global_session_semaphore`, `SessionHandle` |
| `agent/output_handler.py` | `OutputHandler` protocol for routing agent output; `TelegramRelayOutputHandler` (Redis outbox for Telegram delivery) and `FileOutputHandler` (logs to `logs/worker/`) implementations |
| `agent/constants.py` | Canonical location for `REACTION_SUCCESS/COMPLETE/ERROR` (re-exported from `bridge/response.py`) |
| `agent/session_logs.py` | Canonical location for `save_session_snapshot()` (re-exported from `bridge/session_logs.py`) |
| `agent/sdk_client.py` | SDK client; uses `project_key` identity checks for cross-repo detection |
| `worker/__main__.py` | Standalone worker entry point (`python -m worker`); processes sessions without Telegram bridge |

## Project Config Propagation

When a Telegram message arrives, the bridge resolves the full project config from `projects.json` once and passes it downstream. For group messages, `find_project_for_chat()` matches on chat title. For DMs, `find_project_for_dm(sender_id)` is tried first (looks up `dms.whitelist[].project` mapping), falling back to `find_project_for_chat()`. This config is passed through `enqueue_agent_session(project_config=config)` and stored on the `AgentSession.project_config` DictField. At execution time, `_execute_agent_session()` reads the config directly from the session -- no parallel registry or re-derivation needed.

```
Telegram message (group)
    -> find_project_for_chat() resolves full project dict by chat title
    -> enqueue_agent_session(project_config=project_dict)
    -> AgentSession.project_config stores the dict in Redis
    -> _execute_agent_session() reads session.project_config
    -> build_harness_turn_input() receives project dict with all fields

Telegram message (DM)
    -> find_project_for_dm(sender_id) looks up dms.whitelist[].project mapping
    -> falls back to find_project_for_chat() if no per-user mapping
    -> same downstream path as group messages
```

**Cross-repo detection**: `sdk_client.py` uses `project_key != "valor"` to determine whether a session targets a cross-repo project, replacing the previous `project_working_dir != AI_REPO_ROOT` string comparisons.

**Backward compatibility**: Older sessions without `project_config` (created before this field existed) fall back to loading from `projects.json` at execution time. This transitional fallback can be removed after one deploy cycle.

**Config consumers**: `bridge/formatting.py` and `tools/agent_session_scheduler.py` load config from `projects.json` directly via `bridge.routing.load_config()` rather than relying on a module-level registry.

## Migration

- Older AgentSession records in Redis with `session_type="chat"` need migration via `scripts/migrate_session_type_chat_to_pm.py`
- Timestamps (int, float, or ISO string) are auto-converted to UTC-aware datetime via `__setattr__`; invalid types are reset to `None`; run `scripts/migrate_datetime_fields.py` for existing data
- `_normalize_kwargs()` maps old field names to consolidated equivalents
- Workers auto-adapt: jobs with chat_id use per-chat routing; older jobs fall back to project_key
