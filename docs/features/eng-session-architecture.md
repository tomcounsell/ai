# Eng Session Architecture

## Overview

The AgentSession model uses a **session_type discriminator** (`SessionType` enum from `config/enums.py`) to distinguish between session roles:

- **Eng Session** (`session_type=SessionType.ENG`): Full-permission session with Engineer persona, executed by the [headless session runner](headless-session-runner.md) (`agent/session_runner/`). Owns the Telegram conversation, handles SDLC work (planning, coding, testing, review) and conversational responses — a single unified role for both orchestration and execution. For SDLC work, the PM turn spawns and continues a resumable `dev` subagent inline.
- **Teammate Session** (`session_type=SessionType.TEAMMATE`): Conversational session with Teammate persona, same runner. Handles informational queries in DMs and may perform operational work (running scripts, restarting services, editing docs and `.claude/` skills, managing the knowledge base). Writes to source-code paths are blocked in code with a redirect that proposes spawning an Eng session — see [Teammate Session Permissions](teammate-session-permissions.md).
- **Granite** (`session_type=SessionType.GRANITE`): historical enum value only. Pre-cutover records (#1924) carry this value; nothing creates new Granite-typed sessions.

Session types, persona identifiers, and classification types are defined as `StrEnum` members in `config/enums.py`. See [Standardized Enums](standardized-enums.md) for the full enum reference.

The system prompt for each session is assembled by `compose_system_prompt(persona, access_level, ...)` in [`agent/sdk_client.py`](../../agent/sdk_client.py) — the single composer for all session types. Eng sessions resolve to `(ENGINEER, AccessLevel.WORKER)`. This cell carries both the engineer persona and the per-project work-vault `CLAUDE.md` business-context layer (which was previously injected via a separate `PM_READONLY` access level — that level has been removed; the business context now rides `WORKER`). Teammate sessions resolve to `(TEAMMATE, TEAMMATE)`. See [Composed Persona System](composed-persona-system.md) for the full composer signature, the (persona x access-level) matrix, and the byte-stability invariant.

## Routing

Messages are routed to session types via **config-driven persona resolution** (`resolve_persona()` in `bridge/routing.py`), with title-prefix fallback for unconfigured groups:

1. **Config persona** -- if the project's `telegram.groups` dict has a matching entry with a `persona` field, it maps directly: `"engineer"` -> eng, `"teammate"` -> teammate.
2. **Title prefix fallback** -- `"Eng: X"` -> eng mode (backward-compatible prefix).
3. **DMs** -- always resolve to teammate mode via the `dm_persona` field.

Session type derivation from resolved persona:

- **Engineer persona** -> `session_type="eng"` (Eng session, full permissions, engineer persona). Handles both quick conversational questions and SDLC work. The session executes through the [headless session runner](headless-session-runner.md) (`agent/session_runner/`).
- **Teammate persona** -> `session_type="teammate"` (Teammate session, conversational). Handles informational queries directly.

There are three `session_type` values: `eng`, `teammate`, and `granite` (historical only — no live path creates it). The first two are bridge-originated and worker-executed. `session_type` is the **sole discriminator** for routing, permission injection, summarizer formatting, and nudge cap selection. See [Config-Driven Chat Mode](config-driven-chat-mode.md) for the config schema and resolution order.

### Persona resolution on all ingest paths (issue #1708)

Persona resolution applies on **every** ingest path, not just the live Telegram handler:

| Path | Location | Persona-resolved since |
|------|----------|------------------------|
| Live handler | `bridge/telegram_bridge.py` | original |
| Catchup scanner | `bridge/catchup.py` | issue #1708 |
| Reconciler scanner | `bridge/reconciler.py` | issue #1708 |

All three paths call `resolve_persona(project, chat_title)` →
`persona_to_session_type(persona)` (a shared helper in `bridge/routing.py`) and
pass `session_type=` + `project_config=project` to `enqueue_agent_session`.

**Practical consequence:** a chat configured `persona: teammate` in
`projects.json` now runs as `teammate` on *every* ingest path. Before issue
#1708, catchup- and reconciler-re-ingested messages defaulted to
`SessionType.ENG` regardless of the chat's configured persona, causing an
eng PM↔Dev work-loop to run for conversational teammate chats. The fix removes
this asymmetry — the persona the bridge resolves for a live message is
identical to the one resolved for any re-ingested or reconciled message from
the same chat.

A `logger.warning` is emitted at `agent_session_queue.py` when a caller omits
both `session_type` and `project_config` (so the silent `ENG` default remains
but is no longer invisible).

## Enforcement -- Teammate Session Write Restrictions

Teammate sessions (`SESSION_TYPE=teammate`) get the following shape:
**Bash is open** (so teammates can run scripts, restart services, query state)
but **writes to source-code paths are blocked in code** with a redirect that
proposes spawning an Eng session. The `pre_tool_use` hook handles this
enforcement via `_teammate_is_allowed_write()`. The universal allowlist
covers `docs/`, `.claude/`, `.github/`, `wiki/`, `skills/`, top-level meta
files (README, CHANGELOG, CLAUDE.md, LICENSE, etc.) and any top-level `*.md`,
plus the absolute prefix `~/work-vault/`.

The allowlist algorithm runs two passes -- `os.path.normpath` defeats
path-traversal via `..`, then `os.path.realpath` on the parent directory
defeats symlink-escape. The directory rule is anchored to `parts[0]` of
the project-root-relative path (not a substring match), so
`agent/docs_handler/foo.py` does not accidentally match the `docs/` rule.

Bash commands are NOT blocked but are audit-logged with the
`[teammate-audit]` tag at INFO level (truncated to 500 chars). The audit
call is wrapped in try/except so an audit failure cannot block the user.

The block message contains the verbatim `valor-session create --role eng`
command, so the model can surface the redirect to the human directly.

See [Teammate Session Permissions](teammate-session-permissions.md) for
the full design, the allowlist matrix, the threat model (including the
accepted Bash-route escape), and the prompt rewrite that pairs with the
enforcement.

## Architecture

```
Telegram Message
    |
    v
resolve_persona(project, chat_title, is_dm)
    |  1. Config persona lookup (telegram.groups.{name}.persona)
    |  2. Title prefix fallback (Eng:)
    |  3. DMs -> always "teammate"
    |
    |-- Engineer -> Eng Session (session_type="eng")
    |       |-- Full permissions, Engineer persona
    |       |-- Handles both conversational Q&A and SDLC work
    |       |-- Runs via the headless session runner (SessionRunner.run_turn);
    |       |   PM turn spawns/continues its `dev` subagent inline for SDLC work
    |       v
    |   Telegram Response
    |
    |-- Teammate -> Teammate Session (session_type="teammate")
            |-- Conversational, Teammate persona
            |-- Direct answer with operational tools (scripts, service restarts)
            |-- Reduced nudge cap (10)
            v
        Telegram Response
```

## Data Model

Single Popoto model (`AgentSession`) with discriminator field. Popoto ORM does not support model inheritance, so all types share one model with nullable type-specific fields.

### Shared fields (all sessions)
- `id` (AutoKeyField) -- primary key (aliased as `agent_session_id`)
- `session_id` -- Telegram-derived identifier
- `session_type` (KeyField) -- "eng", "teammate", or "granite"
- `status` (KeyField) -- pending/running/active/dormant/completed/failed
- `continuation_depth` (IntField, default 0) -- tracks how many continuation sessions have been chained from the original.
- `project_key`, `created_at`, `history`, etc.
- `project_config` (DictField) -- full project dict from `projects.json`, populated at enqueue time. Carries all project properties through the pipeline so downstream code never re-derives from a parallel registry. Empty/None for older sessions; the worker falls back to loading from `projects.json` at execution time.
- `chat_message_log` (ListField, default `[]`) -- rolling, bounded (50 entries) log of inbound and outbound Telegram chat traffic for this session. Each entry: `{direction, sender, content, message_id, ts}`. Written by the inbound dispatch hook and the relay outbound hook. Read by the message drafter to avoid repeating prior outbound messages. See `docs/features/chat-message-log.md`.

### Eng/Teammate session-specific fields
- `chat_id`, `message_id`, `sender_name`, `message_text` -- Telegram context
- `result_text` -- what was delivered to Telegram

### SDLC-specific fields (on Eng sessions doing pipeline work)
- `parent_agent_session_id` (KeyField) -- canonical parent link (role-neutral)
- `role` (DataField) -- session specialization
- `stage_states` -- derived property reading from `session_events`
- `slug` -- derives branch name, plan path, worktree
- `issue_url`, `plan_url`, `pr_url` -- SDLC link URLs
- `is_ledger` (Field, default `False`) -- marks a `sdlc-local-{N}` anchor as a non-executable ledger record; see [below](#sdlc-local-session-is_ledger-non-executable-flag-issue-2042)

### sdlc-local session `message_text` (issue #1741)

`sdlc-local-{N}` sessions created by `tools/sdlc_session_ensure.py` now carry
an issue-anchored `message_text`. Before issue #1741, `message_text` was not
passed to `create_local`, so the field was `None`. The executor then built the
PM's first turn as "MESSAGE: None", which primed the PM with a phantom task
and caused a silent `[/complete]` no-op — the SDLC pipeline appeared to
succeed but did no work.

The fix populates `message_text` with:

```
Run the full SDLC pipeline for issue #{N}. Read the issue body for the work to be done[ ({issue_url})].
```

This gives the PM a real goal anchor so it can read the issue body, spawn its
`dev` subagent, and drive the pipeline to completion.

**Executor fail-loud guard (pre-SCOPE, `agent/session_executor.py` ~line 1541):**
After steering-message injection and before `build_harness_turn_input` wraps
`_turn_input` in the SCOPE header block, the executor checks the raw turn input.
If `_turn_input` stripped equals `""` or `"None"`, the session is immediately
finalized as `status="failed"` and an `[executor-guard]` ERROR is logged with
reason `empty_container_message`. The session runner's turn dispatch is never
invoked. This guard catches both `None` values and the bare string `"None"`
(which arises from `str(None)`) before they can reach the PM as a phantom task.

### sdlc-local session `is_ledger` non-executable flag (issue #2042)

A human running `/do-sdlc {N}` locally drives the pipeline from a **local
Claude Code session**, not a worker-executed one. That local supervision
still needs somewhere to record SDLC pipeline state for the issue, so it
calls `sdlc-tool session-ensure` (`tools/sdlc_session_ensure.py`), which
creates (or reuses) a deterministic `sdlc-local-{N}` `AgentSession` as the
anchor for stage markers, verdicts, and issue-lock bookkeeping — see
[sdlc-local session `message_text`](#sdlc-local-session-message_text-issue-1741)
above and [SDLC Pipeline State](sdlc-pipeline-state.md).

Before issue #2042, that anchor row was indistinguishable from a real,
interrupted worker session. A live standalone `python -m worker` process
running on the same or another machine could observe the row, mistake it
for orphaned work, reset it to `pending`, and execute it as a `claude -p`
subprocess — producing a second driver racing the local `/do-sdlc`
supervisor on the identical GitHub issue. The result was competing PRs,
`SDLC_HOLDER_TOKEN` collisions, and spurious `ISSUE_LOCKED` errors, all
from a record that was never meant to run at all.

**The fix:** `AgentSession.is_ledger` (`models/agent_session.py`) is a
plain `Field(default=False)` -- not an `IndexedField`, since every guard
site already loads the candidate object before deciding, so an attribute
check is sufficient and no query-by-`is_ledger` is needed.
`tools/sdlc_session_ensure.py` sets `kwargs["is_ledger"] = True` **before**
the single `create_local()` call for every `sdlc-local-{N}` anchor, so the
flag is present in the earliest window a worker could observe the row --
closing the race where a worker claims the row before a follow-up write
would otherwise land.

Eight worker code paths check `is_ledger` and `continue` past the row
instead of acting on it:

| # | Location | Loop |
|---|----------|------|
| 1 | `agent/session_health.py::_recover_interrupted_agent_sessions_startup` | Startup recovery (mechanism 1) |
| 2 | `agent/session_health.py::_agent_session_health_check` (RUNNING loop) | Periodic health check -- guard sits **before** the delivery-finalize exit, which would otherwise flip a ledger anchor to `"completed"` and destroy it |
| 3 | `agent/session_health.py::_agent_session_health_check` (PENDING loop) | Periodic health check |
| 4 | `agent/session_pickup.py::_pop_agent_session` | Worker candidate-selection loop (primary async pop path) |
| 5 | `agent/session_pickup.py::_pop_agent_session_with_fallback` | Sync-fallback candidate-selection loop -- same guard, mirrored into the fallback path so it can't pop a ledger anchor either |
| 6 | `agent/session_health.py::_agent_session_tool_timeout_check` | Per-tool timeout sub-loop (mechanism 10) -- an independent always-on 30s scan of all `status="running"` rows that can finalize a "never started" session via `_apply_recovery_transition`; found during build validation, not in the original audit |
| 7 | `agent/agent_session_queue.py::_check_restart_flag` | Graceful worker-restart gate (issue #2044) -- excludes `is_ledger=True` rows from the running-session count so a ledger anchor no longer defers a restart indefinitely |
| 8 | `agent/agent_session_queue.py::_cli_flush_stuck` | Manual operator flush CLI (issue #2044) -- skips `is_ledger=True` rows in the recovery loop (prints a skip line) so a flush never finalizes/recovers a ledger anchor |

Sites 1-6 were the original #2042 audit; sites 7-8 extended the same guard
to two more scanner loops in `agent/agent_session_queue.py` under issue
#2044. All eight sites share the canonical `_is_ledger()` helper
(`agent/session_health.py`) and the `"... (is_ledger, #2042)"` log/comment
convention, so `grep -rn "is_ledger, #2042"` across the repo finds all
eight.

Every guard site logs `"... (is_ledger, #2042)"` on skip -- `grep "is_ledger,
#2042" logs/worker.log` surfaces every ledger row a worker declined to
touch. See [Session Recovery Mechanisms](session-recovery-mechanisms.md)
for the full catalogue of the mechanisms these guards sit inside.

**Duplicates are accepted as harmless.** If two `sdlc-local-{N}` rows are
ever created for the same issue (a rare concurrent-creation race), both
carry `is_ledger=True` and both are skipped by every guard above -- no
"exactly one anchor" invariant is enforced, and no locking or dedup
mechanism was added on top of the existing `touch_issue_lock()` issue-level
lock (see [SDLC Issue Ownership Lock](sdlc-issue-ownership-lock.md)).

Rows created before issue #2042 read `is_ledger` as `False` via
Popoto's lazy-load descriptor healing (no backfill migration required); a
read-only confirm-style migration
(`_migrate_confirm_is_ledger_field_readable` in
`scripts/update/migrations.py`) proves this on every `/update` run.

### Session Creation
Sessions are created via factory methods:
- `AgentSession.create_eng(...)` -- creates an Eng session
- `AgentSession.create_teammate(...)` -- creates a Teammate session
- `AgentSession.create_child(role=..., ...)` -- generic child session creation

Or directly via `AgentSession.create(session_type="eng", ...)`.

### Derived Properties
- `is_eng`, `is_teammate` -- type checks
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

The Eng session owns all SDLC intelligence. The bridge just keeps it working.

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

Workers are keyed by `worker_key` -- either `project_key`, `slug`, or `chat_id`:
- **Eng sessions**: slugless Eng sessions and Eng sessions at main-checkout stages (PLAN/ISSUE/CRITIQUE/MERGE) use `project_key` and serialize per project. Slugged Eng sessions at worktree stages (BUILD/TEST/PATCH/REVIEW/DOCS) use `slug` and can run concurrently with siblings.
- **Teammate sessions**: always use `chat_id`.

Sessions sharing a working tree serialize; isolated sessions (distinct slugs at worktree stages) can run in parallel. Each issue's build fork exclusively owns `.worktrees/{slug}` and `session/{slug}` from the plan slug -- see [SDLC Fork Turn-Boundary Invariant](sdlc-fork-turn-boundary.md) for the slug-identity-always-wins ownership rule and the turn-boundary invariant that keeps `context: fork` SDLC skills from ending a turn with a live background child.

### Per-Worker-Key Workers
- `_ensure_worker(worker_key, is_project_keyed)` -- starts a worker per key
- `_worker_loop(worker_key, event, is_project_keyed)` -- processes sessions for a key
- `_pop_agent_session(worker_key, is_project_keyed)` -- pops by worker_key
- Callbacks remain per `project_key` (Telegram client is project-scoped)

### Steering Messages
Human replies during active pipelines are buffered as steering messages on the Eng session. The buffer is bounded at 10 messages (oldest dropped on overflow).

## Session Steering

The Eng session can receive mid-execution course corrections via steering messages without waiting for a full turn to complete.

### Mechanism

The Redis steering list (`agent/steering.py`) is the inbox -- any process writes messages via `push_steering_message()`, and the worker injects them at turn boundaries. See [Session Steering](session-steering.md) for the turn-boundary inbox architecture.

```bash
# Steer a running session
valor-session steer --id <session_id> --message "focus on tests"
```

## Q&A Formatting (Prose vs Structured)

The Teammate session type (`session_type="teammate"`) is the branch point for formatting differences.

### Teammate Mode (conversational prose)

When `session_type="teammate"`:
- **Instructions**: `build_teammate_instructions()` in `agent/teammate_handler.py` emphasizes research-first behavior -- search code, query memory, consult docs, cite findings
- **Drafter**: The drafter LLM receives teammate context and produces conversational prose instead of bullets
- **Structured draft bypass**: `_compose_structured_draft()` in `bridge/message_drafter.py` returns the LLM draft directly without emoji prefix, bullet parsing, or structured template
- **Reaction**: Processing reaction is cleared (set to `None`) after delivery instead of setting a completion emoji
- **Single delivery path**: Teammate always goes through the message drafter -- no dual-path ambiguity

### Work Mode (structured formatting)

For Eng sessions:
- **Drafter**: Produces bullet points with status emoji prefix
- **Structured draft**: Full formatting with emoji, stage line (for SDLC), bullets, question section, and link footer
- **Reaction**: Completion emoji set on success

## Key Files

| File | Purpose |
|------|---------|
| `models/agent_session.py` | AgentSession model with session_type discriminator |
| `config/enums.py` | `SessionType`, `PersonaType`, `AccessLevel` enums |
| `agent/agent_definitions.py` | Agent registry (builder, validator, code-reviewer); `validate_agent_files()` verifies expected `.claude/agents/*.md` files are present AND parse cleanly at process startup |
| `agent/agent_session_queue.py` | Queue dispatch surface -- entry points (`enqueue_agent_session`, `register_callbacks`, worker loops); re-exports symbols from split modules |
| `agent/session_completion.py` | Post-execution lifecycle: finalization |
| `agent/session_executor.py` | Core execute loop: `_execute_agent_session()`, turn-boundary steering, nudge/re-enqueue |
| `agent/session_health.py` | Health monitor, startup recovery, orphan cleanup |
| `agent/session_pickup.py` | Pop locking, steering drain, session selection |
| `agent/session_state.py` | Shared globals: `_active_sessions`, `_slot_registry` (`SlotLeaseRegistry`, [Slot-Lease Ownership](slot-lease-ownership.md)), `SessionHandle` |
| `agent/output_handler.py` | `OutputHandler` protocol for routing agent output; `TelegramRelayOutputHandler` (Redis outbox for Telegram delivery) and `FileOutputHandler` (logs to `logs/worker/`) implementations |
| `agent/constants.py` | Canonical location for `REACTION_SUCCESS/COMPLETE/ERROR` |
| `agent/sdk_client.py` | SDK client; `compose_system_prompt` assembles the final system prompt |
| `worker/__main__.py` | Standalone worker entry point (`python -m worker`); processes sessions without Telegram bridge |
| `agent/hooks/pre_tool_use.py` | Hook enforcing Teammate write restrictions |
| `bridge/routing.py` | `resolve_persona()` -- config-driven persona resolution with title-prefix fallback |

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

**Cross-repo detection**: `sdk_client.py` uses `project_key != "valor"` to determine whether a session targets a cross-repo project.

**Backward compatibility**: Older sessions without `project_config` fall back to loading from `projects.json` at execution time.

**Config consumers**: `bridge/message_drafter.py` and `tools/agent_session_scheduler.py` load config from `projects.json` directly via `bridge.routing.load_config()` rather than relying on a module-level registry.
