# Bridge/Worker Architecture

**Status**: Shipped (issue #750)

## Overview

The system separates Telegram I/O from session execution into two independent processes:

- **Bridge** (`bridge/telegram_bridge.py`): Receives Telegram messages, routes them, enqueues `AgentSession` records to Redis. Delivers replies via registered output callbacks.
- **Worker** (`python -m worker`): Polls Redis for pending sessions, executes them via the CLI harness (`claude -p`), handles all session lifecycle functions.

Communication between the two processes happens exclusively through Redis. The bridge never calls worker execution functions; the worker never touches Telegram.

## Data Flow

```
Telegram → Bridge (Telethon)
              ↓ enqueue_agent_session()
           Redis (AgentSession record, status=pending)
              ↓ worker health loop / event
           Worker (_ensure_worker → _worker_loop)
              ↓ Claude Agent SDK
           TelegramRelayOutputHandler
              ↓ branches on session.extra_context["transport"]:
              ↓   transport == "email"  → rpush JSON to email:outbox:{session_id}
              ↓   transport == "telegram" (or unset)
              ↓                         → rpush JSON to telegram:outbox:{session_id}
              ↓ dual-write to FileOutputHandler (logs/worker/) in either case
           Redis outbox (polled by the matching bridge relay)
              ↓ bridge/telegram_relay.py    (telegram:outbox:*)
              ↓ bridge/email_relay.py       (email:outbox:*)
           Delivered to the spawning medium
```

## Worker Output Delivery

The worker uses `TelegramRelayOutputHandler` to deliver session output to Telegram without importing any Telegram client code. This preserves the bridge/worker separation boundary: the worker writes to Redis, and the bridge reads from Redis and delivers via Telethon.

### Output Handler Chain

```
Agent calls tools/send_message.py or tools/react_with_emoji.py
    ↓ invokes OutputHandler.send() / OutputHandler.react()
TelegramRelayOutputHandler.send() (agent/output_handler.py)
    ↓ resolves transport via session.extra_context["transport"]
    │   (defaults to "telegram" when missing or null — back-compat)
    ↓ runs bridge.message_drafter.draft_message (per-medium formatting, length guard)
    ↓ [SDLC sessions] runs bridge.redundancy_filter.should_suppress (issue #1205 — bigram-Jaccard duplicate guard; suppress → 👀 reaction + return)
    ↓ [All sessions] runs bridge.read_the_room.read_the_room (issue #1193 — verdict: send|trim|suppress; opt-in via READ_THE_ROOM_ENABLED)
    ↓ writes JSON payload to the transport-appropriate Redis outbox:
    │     transport == "telegram" → telegram:outbox:{session_id}
    │     transport == "email"    → email:outbox:{session_id}
    │   (or queues a 👀 reaction on suppress, telegram only — see "Reactions" below)
    ↓ also writes to FileOutputHandler (dual-write for audit/fallback)
Redis outbox  → polled by the matching bridge relay
                  bridge/telegram_relay.py  for telegram:outbox:*
                    ↓ after successful send: appends outbound entry to
                      AgentSession.chat_message_log (three-tier session resolution:
                      owner_agent_session_id → real session_id → chat_id lookup)
                  bridge/email_relay.py     for email:outbox:*
                ↓ delivers via Telethon (Telegram) or SMTP (email)
```

**Inbound chat log write:** `bridge/dispatch.py::dispatch_telegram_session` appends an inbound entry to the new session's `chat_message_log` immediately after `enqueue_agent_session` completes. This is the single chokepoint for all Telegram-originating session enqueues. See `docs/features/chat-message-log.md` for the full write/read contract.

**Reactions are telegram-only.** When `extra_context.transport == "email"` the
handler logs once at INFO and skips the reaction (there is no email analog
for an emoji reaction — SMTP has no out-of-band signal channel). This matches
the `EmailOutputHandler.react()` no-op (`docs/features/email-bridge.md`,
"Outbound Drafting" section).

### `TelegramRelayOutputHandler`

Defined in `agent/output_handler.py`. Implements the `OutputHandler` protocol.

| Aspect | Detail |
|--------|--------|
| Redis key (telegram) | `telegram:outbox:{session_id}` |
| Redis key (email) | `email:outbox:{session_id}` (when `extra_context.transport == "email"`) |
| Telegram payload | `{"chat_id", "reply_to", "text", "session_id", "timestamp"}` -- built by `build_telegram_outbox_payload` (shared by `tools/send_message.py`) |
| Email payload | `{"session_id", "to", "subject", "body", "in_reply_to", "references", "from_addr", "attachments", "timestamp"}` -- the unified shape consumed by `bridge/email_relay.py` (see [Email Bridge](email-bridge.md) "Send path"). The handler reads `email_subject`, `email_message_id`, `email_to_addrs`, `email_cc_addrs` from `session.extra_context` to populate `subject`, `in_reply_to`, and the reply-all `to` list. `tools/send_message.py::_send_via_email` delegates to this handler rather than emitting its own payload (issue #1369). |
| TTL | 3600 seconds (1 hour) |
| Redis operation | `RPUSH` (append to list) + `EXPIRE` |
| Error handling | Caught and logged; never propagates to caller |
| Dual-write | Wraps `FileOutputHandler` internally for local log persistence |

### Registration

At worker startup (`worker/__main__.py`), `TelegramRelayOutputHandler` is created with a `FileOutputHandler` as its inner handler, then registered for every project via `register_callbacks()`. It serves as the **default** output path for any session whose project has not registered a transport-specific handler.

The handler is itself transport-aware: on each `send()` it resolves `session.extra_context["transport"]` and writes to the matching outbox (`email:outbox:*` for email-spawned sessions, `telegram:outbox:*` otherwise). This means an email-spawned session reaches the SMTP relay correctly even when the project has no per-`(project_key, "email")` `EmailOutputHandler` registered — the default handler picks the right queue from the session's transport context.

When a project does have an explicit `EmailOutputHandler` registered (via `register_callbacks(project_key, transport="email", handler=...)`), the worker resolves that handler instead and bypasses the outbox entirely (it sends directly via SMTP from the worker process). See [Email Bridge](email-bridge.md) "Worker Registration" and "Send path" for the registered-handler path.

### Relationship to `FileOutputHandler`

`FileOutputHandler` is not replaced -- it is wrapped. Every call to `TelegramRelayOutputHandler.send()` or `.react()` also forwards to the inner `FileOutputHandler`, so output is always persisted to `verify actual log path or remove if not implemented` even if Redis is unavailable. This dual-write pattern provides:

- **Audit trail**: Local logs survive Redis TTL expiry.
- **Fallback**: If Redis is down, the output is still captured on disk (though not delivered to Telegram).
- **Dev environments**: On machines without a bridge, the file log is the only record.

## Bridge Responsibilities (only)

1. Authenticate with Telegram and receive messages
2. Route messages to projects via `find_project_for_chat()`
3. Call `enqueue_agent_session()` — writes `AgentSession` to Redis
4. Register output callbacks via `register_callbacks(project_key, handler=...)`
5. Deliver replies via Telegram when callbacks fire
6. Run `KnowledgeWatcher` for work-vault file change monitoring
7. Run message catchup scan and reconciler on startup
8. Write bridge liveness signals to Redis for the external watchdog (see [Bridge Self-Healing](bridge-self-healing.md#3a-update-loop-wedged-detector-issue-1712)):
   - `bridge:last_update_received` — written by the `NewMessage` handler before dedup; staleness while `bridge:last_probe_ok` is fresh indicates the Telethon update loop has wedged
   - `bridge:last_probe_ok` — written by the reconciler after each successful `get_dialogs()` call; distinguishes a wedged update loop from a full TCP/API disconnect

The mechanical catchup (`bridge/catchup.py`) and reconciler (`bridge/reconciler.py`) cover **ingestion gaps** — messages that never got a session enqueued. They cannot recover **response failures** (session enqueued, hung/killed, no reply) because the `DedupRecord` entry already exists. The [Agent-Judgment Catchup](agent-judgment-catchup.md) is the response-failure complement: it reads the actual chat thread (including Valor's own `out` replies), uses an LLM judge to classify unanswered messages, and enqueues recovery sessions. It runs out-of-band via `valor-catchup` and as the final best-effort step of `/update`.

The bridge does **not**:
- Call `_ensure_worker()`, `_recover_interrupted_agent_sessions_startup()`, `_agent_session_health_loop()`, `_session_notify_listener()`, `_cleanup_orphaned_claude_processes()`, `_reap_orphan_session_processes()`, or `register_worker_pid()`
- Call `AgentSession.rebuild_indexes()`
- Poll Redis for orphaned sessions
- Kill or manage Claude SDK subprocesses

## Worker Responsibilities (only)

The worker's startup sequence is deterministic:

| Step | Function | Purpose |
|------|----------|---------|
| 1 | `AgentSession.rebuild_indexes()` | Repair stale/corrupt Redis index entries |
| 2 | `cleanup_corrupted_agent_sessions()` | Remove malformed session records and reap cross-process orphan `claude`/MCP processes; phantom-filter guarded and calls `repair_indexes()` to clear orphan `$IndexF` members (issues #1069, #1271). Returns `{"corrupted": int, "orphans": int}`. Also prunes JSONL telemetry files under `logs/session_telemetry/` older than 14 days (see [Session Telemetry](session-telemetry.md)). |
| 3a | `_sweep_dead_worker_sessions()` | Finalize `running` sessions whose `claude_pid` is dead (`os.kill(pid, 0)` raises `OSError`) to `killed`, then trigger `bridge.agent_catchup` so unanswered messages re-enqueue (issue #1767). **Must run before Step 3b** — once 3b resets all `running` sessions to `pending`, there are no `running` sessions left to inspect by PID. |
| 3b | `_recover_interrupted_agent_sessions_startup()` | Reset remaining `running` sessions to `pending` (orphaned from prior process with a live or absent PID) |
| 3.5 | `register_worker_pid()` | Write `worker:registered_pid:{hostname}:{pid}` (TTL 24h) so the cross-process reaper's skip-set excludes this worker (issue #1271 self-suicide guard) |
| 4 | `_cleanup_orphaned_claude_processes()` | Backward-compat shim — delegates to `_reap_orphan_session_processes()`. The hourly `agent-session-cleanup` reflection now covers the same OS-table scan, so startup is no longer the only call site (issue #1271). |
| 4.5 | `verify_harness_health()` | Verify CLI harness binary (`claude`) is available and healthy; fatal if missing (see [Harness Abstraction](harness-abstraction.md)) |
| 5 | `_ensure_worker(worker_key)` for each pending session | Kick per-worker-key loops for queued sessions |
| 6 | `_agent_session_health_loop()` | Background task: periodic session health checks, orphan detection (safety net) |
| 7 | `_session_notify_listener()` | Background task: subscribe to `valor:sessions:new` pub/sub, wake worker on new session (~1s pickup) |

Step 8 (the idle sweeper, issue #1128) was retired in #2000 — see [Worker-Internal Idle Sweeper](#worker-internal-idle-sweeper-issue-1128) below.

### Worker-Process Sentry (issue #1877)

Session execution happens in the worker, so worker-side exceptions (SDK,
tool, and lifecycle crashes) need the same Sentry visibility as bridge
exceptions. Before issue #1877, `sentry_sdk.init()` was called only from
`bridge/telegram_bridge.py`; the worker had no Sentry init at all, so every
exception during session execution — including a silent running→failed
crash — was invisible to Sentry.

The fix extracts the bridge's init block into a shared
`configure_sentry(component, before_send=None)` helper in
`monitoring/sentry_config.py`, called by both processes at startup: the
bridge from its module-level init block, the worker from
`worker/__main__.py::main()` before `asyncio.run(_run_worker(...))`.

- **DSN-gated, verbatim.** If `SENTRY_DSN` is unset the helper returns
  without initializing — the same gating the bridge already had. `release`
  (git HEAD), `traces_sample_rate`, and `environment` are preserved
  unchanged.
- **`before_send` is a parameter, not hardcoded.** The bridge passes its
  `_sentry_before_send`, which drops events while the *bridge* is
  hibernating (see `docs/plans/sentry_hibernation_filter.md`) — a
  bridge-only concept. The worker passes `before_send=None` so worker
  Sentry events are never silently dropped just because the bridge happens
  to be hibernating while the worker itself is healthy. `configure_sentry`
  never imports `bridge.hibernation`.
- **Minimal pytest/CI guard only — no machine gate.** `configure_sentry`
  returns early when `PYTEST_CURRENT_TEST` or `CI` is set, so a
  `SENTRY_DSN`-present worker test run never mis-tags events as
  `production`. This is deliberately the *only* environment gate in the
  helper — there is no machine/platform check. The richer dev-vs-prod
  environment gating is a separate concern (issue #1834) that layers on top
  of this helper later; #1877 does not block on it.
- **Env propagation needs no update-system changes.** The worker is a
  separate launchd process, but `scripts/install_worker.sh` and
  `scripts/remote-update.sh` already inject **all** `.env` values —
  including `SENTRY_DSN` / `SENTRY_ENVIRONMENT` — into the worker plist's
  `EnvironmentVariables` via `dotenv_values`, exactly as they do for the
  bridge plist. `os.getenv("SENTRY_DSN")` therefore resolves correctly in
  the launchd worker with no additional wiring.
- **Startup observability.** The worker logs its enabled/disabled state at
  startup: `worker sentry: enabled` or
  `worker sentry: disabled (no DSN in worker env)`, so a missing-env no-op
  is visible in `logs/worker.log` rather than silent.

### Media Enrichment (issue #1297)

Telegram media (photos, voice, audio, documents) requires both Telethon RPC (download) and an AI call (vision / Whisper / extraction). The two halves are split along the bridge/worker boundary:

- **Bridge** runs `download_media(client, message)` synchronously at intake, with a 10-second `asyncio.wait_for` timeout. The downloaded file's absolute path is persisted on `TelegramMessage.media_local_path`. On timeout or error, `media_download_error` is populated instead. Net intake-latency cost: ~200ms-1s for media messages, zero for text-only.
- **Worker** reads `TelegramMessage.media_local_path` from the persisted record (no Telethon import in `worker/`) and calls `bridge.media.process_downloaded_media(path, media_type)` for the AI half. The worker also handles every "skipped" branch (download failed, file unreadable, no record) and emits a single `[enrichment] Summary: media=...` log line per session.

Full contract and field-level reference live in [media-enrichment.md](media-enrichment.md). The sibling reply-chain branch in `bridge/enrichment.py` still requires a Telethon client and is silently skipped in the worker until a follow-up issue lands; that is **not** considered fixed by sdlc-1297.

### Media Intake Resilience (issue #1330)

The sdlc-1297 contract assumes (a) `TelegramMessage.query.get(stored_msg_id)` always returns the record the bridge just created, and (b) `_download_media_with_retry` returns either a path or a populated error string. Both assumptions can fail transiently — a Popoto stale-index condition can return `None` from `query.get`, and the size-aware retry wrapper can return `(None, None)` ("no_path" outcome) when `client.download_media` reports success but the file is missing post-download. Either case left the file orphaned on disk and the agent seeing only the `[media]` placeholder.

Defense in depth lives in two layers:

- **Bridge persist (loud failure)**: After `query.get(stored_msg_id)`, a single bounded re-query covers transient stale-index reads; if both calls return `None`, the bridge logs a `WARNING` with `stored_msg_id`, `chat_id`, `message_id`, `local_path`, and `download_error` and proceeds. Separately, if the download wrapper returned `(_local_path=None, _download_error=None)` while `message.media` is truthy, a second `WARNING` records the no-path-no-error condition. Neither path is silent anymore. The bridge deliberately does **not** call `query.keys(clean=True)` here — index-mutating calls on the hot intake path are too expensive; the worker-side fallback below is the durable safety net.
- **Worker self-heal**: In `bridge/enrichment.py`, when `has_media=True` AND `media_local_path` is unset AND `media_download_error` is also unset, the worker globs `bridge.media.MEDIA_DIR` for `*_{message_id}.*` (the filename pattern is fully determined by `bridge/media.py::download_media`). On exactly one match it adopts the file with an `INFO` log (`self-heal: recovered orphan media file ...`) and runs AI enrichment as if `media_local_path` had been persisted. Zero matches or multiple matches fall through to the existing "older record?" `WARNING` so ambiguity surfaces rather than silently misroutes.

**Explicit non-goal**: this work does not fix the upstream Popoto stale-index root cause. That is tracked under #617 / #860 — orphan-index hygiene is its own ongoing reflection. sdlc-1330 makes intake resilient to that condition; the root cause is unaffected.

### Classification Key-Resolution Resilience (issue #1899)

Inbound messages trigger up to three classification calls that share one key resolver, `utils/api_keys.py::get_anthropic_api_key`: the terminus decision (`bridge/routing.py::classify_conversation_terminus`), the background work-type classifier (`tools/classifier.py::classify_request_async`, fired non-blocking from `classify_work_type()`), and the intent classifier (`agent/intent_classifier.py`). A message never depends on classification succeeding — each path carries a message-preserving default:

- **Terminus → `RESPOND`**: the Haiku fallback is guarded by `if api_key:` and defaults conservatively.
- **Work-type → sentinel `type=None`**: a missing key logs a single `WARNING` and returns `{"type": None, "confidence": 0.0, "reason": "..."}`. The bridge maps `type=None` to its most conservative routing (`"question"`, no spurious SDLC spawn). Genuine API/parse failures keep their `ERROR`-level (Sentry-visible) logging.
- **Intent → `new_work`**: defaults internally on any error.

**Resolver self-heal**: `get_anthropic_api_key()` returns `str | None` and caches **only a truthy** resolution. An absent resolution returns `None` without caching it, so the next call re-reads env/`.env` rather than short-circuiting on a poisoned empty cache. This removes a persistence amplifier: a transient startup window (LaunchAgent env or `.env` symlink not yet readable when the first classification ran) previously cached `""` and forced every subsequent classification in that process to fail until restart. Now the miss clears itself on the next inbound message once the environment settles. A *permanently* keyless process is not hidden by the work-type `WARNING` downgrade — the same resolver feeds the terminus fallback, the intent classifier, the health check, and every live session's model client, so a genuinely keyless bridge surfaces loudly through those paths.

### Execution Harness Routing

All session types (dev, pm, teammate) execute via the CLI harness (`claude -p`). There is no SDK execution branch — the `DEV_SESSION_HARNESS` feature flag was eliminated in issue #912.

```
_execute_agent_session(session)
    |
    |-- _get_prior_session_uuid(session_id)  -- Popoto lookup of claude_session_uuid (#976)
    |
    v
build_harness_turn_input(skip_prefix=bool(prior_uuid))
    |   -- first turn: full PROJECT/FROM/SCOPE/MESSAGE headers
    |   -- resumed turn: raw new message only (binary has context from its session file)
    |
    v
_apply_context_budget()     -- trims if input exceeds HARNESS_MAX_INPUT_CHARS (100K); runs on every call
    |
    v
get_response_via_harness(prior_uuid=..., session_id=..., full_context_message=...)
    |   -- resumed turn: spawns `claude -p --resume <uuid> [raw_message]`
    |   -- first turn:   spawns `claude -p [full_context_message]`
    |   -- stale UUID:   on any non-zero exit with prior_uuid set, retries once without --resume
    |   -- after success: persists captured session_id to AgentSession.claude_session_uuid
    |
    v
complete_transcript(session_id, status=final_status)
    |   -- synchronous call: calls finalize_session() → _finalize_parent_sync() inline
    |   -- for a child session, transitions the parent: running →
    |      waiting_for_children → completed/failed once all children are terminal
    |   -- telemetry: turn_end/token_usage events tapped in sdk_client.py; status_transition
    |      event emitted by finalize_session(); JSONL written to logs/session_telemetry/
    |      (see Session Telemetry)
```

**Child-to-parent completion**: when a session has a `parent_agent_session_id`, `complete_transcript()` → `finalize_session()` synchronously calls `_finalize_parent_sync()` (in `models/session_lifecycle.py`). That function moves the parent into `waiting_for_children`, then — once every child is terminal — transitions it to `completed` (all children succeeded) or `failed` (any child failed). It is idempotent and a no-op if the parent is already terminal. If the completion-turn runner is in flight for the parent (the `pipeline_complete_pending:{parent_id}` Redis lock is held), `_finalize_parent_sync` defers the success-path transition to that runner so the final summary is delivered exactly once (issue #1058). There is no separate post-completion SDLC handler — every session type finalizes through this single path.

See [Harness Abstraction](harness-abstraction.md) for stream-json parsing, chunk suppression, health checks, and configuration, and [Harness Session Continuity](harness-session-continuity.md) for the `--resume` UUID persistence mechanism.

At runtime, the worker processes sessions via `_worker_loop(worker_key)` until the queue is empty, then waits for new enqueue events.

### Persona Overlay Resolution (harness `--append-system-prompt`)

> **Bridge-originated sessions (PM, Dev, Teammate) bypass this path entirely.** Every session role receives its persona via role prime commands (`.claude/commands/roles/prime-*-role.md`) run by the [headless session runner](headless-session-runner.md) at turn 1 — no `--append-system-prompt` is set. The description below applies only to the direct `claude -p` path used outside the runner (e.g. the message drafter).

`agent/session_executor.py` resolves which persona overlay to pass as the harness `system_prompt` (`--append-system-prompt`) on that direct path. The order is:

1. `transport=email` or `project["email"]["persona"]` set → that persona overlay (e.g. `customer-service`), with `teammate` as fallback when the requested overlay file is missing
2. `session_type=ENG` → `engineer` overlay (loaded via `load_eng_system_prompt`)
3. `session_type=TEAMMATE` (Telegram DM/teammate) → `teammate` overlay

The resolution emits a single canonical INFO line BEFORE any disk read so absence-vs-fallback is visible in `logs/worker.log` without triangulating against the downstream "Persona overlay loaded" or "Appending N-char system prompt" lines:

```
agent.session_executor INFO [<cid|project>] Resolved persona for session=<sid>: <name|<none>> (source=<source>)
```

If `project["email"]["persona"]` is set but neither the requested overlay nor the fallback can be loaded, the resolver emits an `ERROR [persona-load-failed]` line — the harness will run with no system prompt and reply in the default voice, so the operator should review the queued `email:outbox:` payload before SMTP relay. See [email-bridge.md](email-bridge.md#persona-resolution-for-email-spawned-sessions) for the full rule.

### Worker-Internal Idle Sweeper (issue #1128) — Retired in #2000

The worker used to own an `_active_clients` registry mapping `session_id → ClaudeSDKClient` for
the persistent-connection SDK path, and ran an idle-sweeper background task
(`worker/idle_sweeper.py::run_idle_sweep`, supervised in `worker/__main__.py`) to proactively
tear down those connections before Anthropic's ~48h silent-death window (fleet-ops finding
#1104). Every production session (PM / Dev / Teammate) has run through the harness path — a
short-lived `claude -p` subprocess per turn — for some time, which never populated
`_active_clients`, so the sweeper was already a permanent no-op there. #2000 deleted the
sweeper, its `worker/__main__.py` supervision wiring, and the `_active_clients` registry
wholesale, along with the rest of the dead Claude Agent SDK path (see
[HarnessAdapter Seam](harness-adapter.md)). There is nothing left to sweep on the harness path.

## Worker Key Routing (issues #831, #1085)

Workers are keyed by `worker_key` — a computed property on `AgentSession` that reflects the session's actual isolation level, not its Telegram communication topology. This prevents sessions that share mutable state (the git working tree) from racing each other across Telegram threads.

### Decision Table

| Session type | Slug? | Stage | `worker_key` | Behavior |
|---|---|---|---|---|
| `pm` | no | any | `project_key` | Serialized per project |
| `pm` | yes | PLAN / ISSUE / CRITIQUE / MERGE | `project_key` | Serialized per project (shares main checkout) |
| `pm` | yes | BUILD / TEST / PATCH / REVIEW / DOCS | `slug` | Parallel per slug (each PM in its own worktree) |
| `dev` | yes | any (worktree) | `slug` | Parallel-safe across chats AND across slugs in the same chat |
| `dev` | no | any (main repo) | `project_key` | Serialized per project |
| `teammate` | N/A | N/A | `chat_id` | Always parallel-safe |

**Eng routing note:** Slugged eng sessions use an allowlist (`_ENG_WORKTREE_STAGES = {"BUILD", "TEST", "PATCH", "REVIEW", "DOCS"}`) to determine when slug-based routing is safe. Stages not in this allowlist — including PLAN, ISSUE, CRITIQUE, MERGE, unknown/future stages — fall back to `project_key` so they serialize on the main checkout. Unknown stages fail closed (serialize) rather than accidentally parallelizing on an unaudited stage. The lazy `_ensure_worker` call in `session_pickup.py`'s project-keyed pop handles the routing gap: when an eng session advances to a worktree stage and the project-keyed filter rejects it, the correct slug-keyed worker is started automatically.

### Why `chat_id` Is Not the Isolation Key

`chat_id` is a communication topology concept — it tells you which Telegram thread a message came from. But isolation depends on whether sessions share mutable state (the git working tree). Two PM sessions from different threads both write to the same `main` branch at PLAN stage; they must serialize regardless of their `chat_id`.

Similarly, `chat_id` is insufficient for eng sessions — two eng sessions for different work items in the same chat (e.g., two `valor_session create --role eng` calls defaulting to `chat_id=0`) would serialize even though they share no state. Slug is the correct routing key for worktree-isolated eng sessions: each slug has its own worktree and branch.

### Three Worker Loop Archetypes

1. **Project-keyed worker** (`worker_key == project_key`): Handles slugless PM sessions, PM sessions at main-checkout stages (PLAN/ISSUE/CRITIQUE/MERGE), and dev sessions without a slug. These share the main repo working tree and must run one at a time per project. The `_pop_agent_session` function filters by `project_key` and only pops sessions whose `worker_key` matches. When it encounters a PM session that has advanced to a worktree stage (`worker_key != project_key`), it starts the appropriate slug-keyed worker lazily.

2. **Chat-keyed worker** (`worker_key == chat_id`): Handles teammate sessions. Teammate sessions are conversational and have no shared mutable state — different `chat_id`s run in parallel.

3. **Slug-keyed worker** (`worker_key == slug`): Handles slugged dev sessions and slugged PM sessions at worktree stages. Each slug has its own worktree (`.worktrees/{slug}/`) and branch (`session/{slug}`), so two sibling sessions with distinct slugs route to distinct worker loops and run in parallel. `_pop_agent_session` attempts a `slug=worker_key` indexed lookup first for non-project-keyed workers, falling back to `chat_id=worker_key` for teammate sessions.

### `is_project_keyed` Discriminator

Since `worker_key` is an opaque string, callers pass `is_project_keyed: bool` alongside it so `_pop_agent_session` can use the correct filter predicate. This avoids fragile string-comparison against known project keys.

## Worker Serialization and Deduplication

Each `worker_key` has at most one active `_worker_loop` task at any time. This is the **serialization invariant**: all sessions belonging to the same worker key are processed strictly in FIFO order, never concurrently. The invariant is enforced by `_ensure_worker()` through a dual-guard mechanism:

| Guard | What it covers |
|-------|---------------|
| `_active_workers[worker_key]` | **Steady-state**: task exists and `.done()` is False — already running, do nothing. |
| `_starting_workers` (set) | **Startup race**: `create_task()` has been called but the task has not yet registered itself in `_active_workers`. A second call that arrives in the same event-loop turn sees this flag and returns without spawning another task. |

Because `_ensure_worker()` is a plain synchronous function (no `await`), the check-and-set of both guards is atomic within the cooperative asyncio event loop. This is particularly important during the health-check loop, which may iterate many pending sessions sharing the same `worker_key` and call `_ensure_worker()` for each one before any task is live in `_active_workers`.

**Lifecycle of `_starting_workers`:**

1. Added immediately before `asyncio.create_task()`.
2. Cleared unconditionally in the enclosing `try/finally` block, so `_starting_workers.discard(worker_key)` runs on every exit path — success, `create_task()` failure, or any post-create statement failure — guaranteeing the guard never leaks. If a post-create statement raises after the task exists but before it is published to `_active_workers`, the task is cancelled in the `except` branch so no orphan runs.

The `_worker_loop` removes itself from `_active_workers` in its `finally` block. After it exits, the next call to `_ensure_worker()` (triggered by the next enqueue or the health check) starts a fresh task.

## Concurrency Controls (issue #810)

### Per-Worker-Key Serialization Guarantee

Sessions belonging to the same `worker_key` always execute **strictly one at a time**. This is enforced by the serialization invariant: each `worker_key` has exactly one `_worker_loop` task (see Worker Serialization above), and that task pops and executes sessions sequentially.

### Global Session Ceiling (`MAX_CONCURRENT_SESSIONS`)

A global, owner-keyed `SlotLeaseRegistry` limits how many sessions can execute simultaneously across **all** worker keys and **all** session types:

```bash
# Set the ceiling (default: 8)
MAX_CONCURRENT_SESSIONS=5 python -m worker

# Or in .env
MAX_CONCURRENT_SESSIONS=8
```

**Implementation details:**
- `_slot_registry` is a module-level `SlotLeaseRegistry | None` in `agent/session_state.py`, wrapping one `asyncio.Semaphore` for backpressure plus an `{owner_session_id: Lease}` map (issue #1820, replacing the prior ownerless `_global_session_semaphore`). See [Slot-Lease Ownership](slot-lease-ownership.md) for the full design.
- Initialized by `_run_worker()` in `worker/__main__.py` **before** any worker loop is created
- Clamped to minimum 1 to prevent deadlock (`MAX_CONCURRENT_SESSIONS=0` → 1 with a warning log)
- `registry.acquire()` blocks **before** `_pop_agent_session()` is called, so `transition_status("running")` never occurs without a slot — the dashboard count stays accurate. `registry.bind(owner)` records the lease synchronously right after the pop resolves a session (no lease exists during the pop gap itself)
- Released via `registry.release(owner)` after `_execute_agent_session()` completes (in the `finally` block, via all code paths including `CancelledError`); an out-of-band kill instead calls `registry.reclaim(owner)` from `_apply_recovery_transition`, and a hoisted top-of-tick reap pass in the health check reclaims any lease still held by a session that has already reached a terminal status
- When `None` (e.g., in tests that don't call `_run_worker()`), no ceiling applies

**PM/dev deadlock prevention:** There is no session-type-specific cap. PM sessions that spawn child dev sessions transition to `waiting_for_children`, which triggers `output_router.route_session_output` to return `"deliver"` — the PM releases its global slot before the child needs it. Child dev sessions sort ahead of peers via the child-boost ordering in `sort_key`, so they acquire the freed slot next. See issues #1004 and #1021 for the history.

**Wedge detection and self-heal:** When the registry is exhausted (`registry.permits_free() == 0`) and running sessions are fewer than `MAX_CONCURRENT_SESSIONS`, the health monitor's hoisted reap pass (`_agent_session_health_check`) emits a `WARNING` leaked-slot fingerprint log line, then reclaims any lease whose owner has reached a terminal status — no process restart required (issue #1820, closing the #1537/#1808 leak class the old logging-only fingerprint could only report on). See [Worker Wedge Investigation](worker-wedge-investigation.md) for the root-cause analysis and [Slot-Lease Ownership](slot-lease-ownership.md) for the reclaim path.

### Redis Pop Lock (TOCTOU Prevention)

A short-lived Redis lock (`SETNX worker:pop_lock:{worker_key}`) wraps the query→transition block in both pop paths:

| Pop path | Lock coverage |
|----------|--------------|
| `_pop_agent_session()` | Wraps `async_filter(status="pending")` + `transition_status("running")` |
| Sync fallback in `_pop_agent_session_with_fallback()` | Wraps `query.filter(status="pending")` + `transition_status("running")` |

**Properties:**
- TTL = 5 seconds (well above any realistic Redis write latency; self-heals on crash)
- If lock is held: returns `None` immediately (caller will retry on next event-loop iteration)
- Fail-open: if Redis is unreachable, `_acquire_pop_lock()` returns `True` so workers are not blocked
- The two paths are **not re-entrant**: `_pop_agent_session()` acquires, does its work, and **releases** the lock before returning. The sync fallback branch only runs after `_pop_agent_session()` returns `None` (lock already released), so it acquires a fresh lock — no nesting.

### CLI Session Isolation (`create_local()`)

> **Scope after #1157:** `create_local()` is only called by the `UserPromptSubmit` hook for **direct-CLI subprocesses** (developer running `claude` at the terminal with `SESSION_TYPE` / `VALOR_PARENT_SESSION_ID` exported). Worker-spawned PM/Teammate/Dev subprocesses never reach this path — the hook attaches the sidecar to the worker's pre-existing AgentSession instead (see [Hook-Layer Session Attach](#hook-layer-session-attach-issue-1157) below).

Direct-CLI sessions created by `verify method exists at this location` use the **Claude Code session UUID** as `chat_id` instead of a collision-prone modulo timestamp:

```python
# Before (collision-prone): same chat_id for sessions created within same 2.7-hour window
chat_id = f"local{int(now.timestamp()) % 10000}"

# After (unique): each CLI session gets its own isolated queue
chat_id = session_id  # Claude Code UUID (e.g., "abc123-def456-...")
```

This ensures that multiple CLI sessions (e.g., parallel `/do-build` runs) each get their own worker queue and never serialize with each other.

### Hook-Layer Session Attach (issue #1157)

Worker-spawned subprocesses produce exactly ONE `AgentSession` row — the worker-created record. The `UserPromptSubmit` hook at `.claude/hooks/user_prompt_submit.py` does NOT mint a duplicate `local-*` record when the worker has already created one.

The worker communicates ownership by setting two env vars before `subprocess.Popen` (see `verify code range or remove line-specific reference`):

- `AGENT_SESSION_ID` — the worker's `agent_session_id` UUID (primary signal)
- `VALOR_SESSION_ID` — the worker's bridge `session_id` (fallback signal)

On the subprocess's first prompt, the hook:

1. Reads `AGENT_SESSION_ID` and resolves it via `AgentSession.get_by_id()` (indexed, O(1)).
2. Falls back to `VALOR_SESSION_ID` via `query.filter(session_id=...)` if the primary misses.
3. If resolved and non-terminal: writes that `agent_session_id` into the sidecar and returns. **The `create_local()` call is never reached.**
4. If the resolved target is terminal (killed/completed/failed/abandoned/cancelled): falls through to the existing `create_local` gate, preserving #1113 terminal-session semantics (no zombie revival).
5. If neither env var resolves: falls through to the existing gate, which blocks the creation unless `SESSION_TYPE` or `VALOR_PARENT_SESSION_ID` is set (direct-CLI path from #1001).

The `PostToolUse` and `Stop` hooks use the same sidecar-first lookup pattern: primary via `AgentSession.get_by_id(sidecar_agent_session_id)`, with a fallback via `query.filter(session_id=f"local-{claude_session_id}")`. The fallback is retained because direct-CLI sessions still write `local-*` records.

**Race prevention invariant:** the worker's `AgentSession.create()/save()` must complete synchronously before `subprocess.Popen`. Python interpreter startup + hook import + Redis connection take hundreds of ms, while Redis commits are sub-ms; the race is theoretically possible but practically impossible. If the race ever fires, the hook falls through to `create_local()` (status quo, not worse than before).

## Session Pickup: Fast Path vs Safety Net

The worker uses two mechanisms to discover new sessions:

| Mechanism | Latency | How It Works |
|-----------|---------|-------------|
| **Redis pub/sub** (fast path) | ~1 second | `_push_agent_session()` publishes `{"chat_id", "session_id", "worker_key", "is_project_keyed"}` to `valor:sessions:new`. `_session_notify_listener()` subscribes and calls `_ensure_worker(worker_key)` immediately. |
| **Health check loop** (safety net) | Up to 10 minutes | `_agent_session_health_loop()` fires every 300s. Sessions pending longer than 300s trigger `_ensure_worker(worker_key)` recovery. |

The fast path covers normal operation. The health check catches edge cases: missed pub/sub messages (network blip, worker restart during publish), sessions created by paths that bypass `_push_agent_session()`, and sessions orphaned from a prior worker process.

**Bridge path**: `enqueue_agent_session()` → `_push_agent_session()` publishes notification → worker receives within ~1s.

**CLI path** (`python -m tools.valor_session create`): Same — `_push_agent_session()` publishes to `valor:sessions:new` → worker receives within ~1s. Prior to issue #778, CLI-created sessions relied solely on the health check (worst case: 10 minutes).

**Implementation note**: `_session_notify_listener` uses a **dedicated** `redis.Redis` connection (created inside `_listen_in_thread`) with `socket_timeout=None` and `socket_connect_timeout=None`. It reads `host`/`port`/`db` from `POPOTO_REDIS_DB.connection_pool.connection_kwargs` but passes both timeout parameters explicitly. This is required because the global `POPOTO_REDIS_DB` pool has `socket_timeout=settings.timeouts.redis_socket_s` (default 5s, tuned for request-response commands, `.env`-overridable via `TIMEOUTS__REDIS_SOCKET_S` — see [Config Timeout Catalog](config-timeout-catalog.md)), which would cause the blocking `pubsub.listen()` iterator to raise a socket timeout exception after the configured idle window — triggering an unnecessary reconnect cycle with a dead window during which any published notification would be lost (issue #824).

**Subscribe-time NUMSUB self-verification (issue #1804)**: after `pubsub.subscribe()` returns, `_listen_in_thread` verifies `PUBSUB NUMSUB valor:sessions:new >= 1` on the same `socket_timeout=None` connection. It retries up to 3 times (~300 ms total) to absorb registration latency. If NUMSUB still reports 0, a WARNING is logged and the function returns early — falling through the existing `finally` teardown — so the outer `while True` loop re-subscribes after its 5 s backoff. All of this runs in the listener's own thread (no cross-thread machinery). Post-subscribe drift (NUMSUB → 0 after a previously-good subscribe) is left to the existing 300 s `_agent_session_health_check` backstop (`agent/session_health.py`), which re-scans `pending` sessions and nudges/starts workers.

**`VALOR_WORKER_MODE=standalone` in worker plist**: `com.valor.worker.plist` now sets `VALOR_WORKER_MODE=standalone` in `EnvironmentVariables`. The runtime behavior was already standalone (via `os.environ.setdefault("VALOR_WORKER_MODE", "standalone")` in `worker/__main__.py:main()` before the worker loop), but the explicit plist entry makes `ps eww` inspection unambiguous — the variable is visible in the launchd launch environment, not just the mutated runtime environment.

## Worker Restart Recovery

Worker restarts (SIGTERM, crash, or explicit `verify command exists or correct syntax`) are non-destructive. Sessions in `pending` or `running` state at restart time are both preserved and will be executed by the new worker process.

### `pending` sessions survive restarts untouched

`_cleanup_stale_sessions()` only iterates sessions in `running` state. A `pending` session has never been assigned to a worker process — there is no stale process to clean up. The new worker picks up pending sessions from the queue naturally as part of normal operation.

### Interrupted `running` sessions are re-queued on next startup

When the worker process is killed mid-execution, the `asyncio.CancelledError` handler does **not** finalize the session. The session remains in `running` state in Redis. On the next worker startup, two steps handle stale `running` sessions in order:

1. **Step 3a — `_sweep_dead_worker_sessions()`** (issue #1767): checks `claude_pid` liveness via `os.kill(pid, 0)`. Sessions whose subprocess is provably dead are finalized to `killed` and `bridge.agent_catchup` is triggered so the user's unanswered message re-enqueues. This step must run first — otherwise Step 3b would reset all `running` sessions to `pending` before PID liveness can be checked.
2. **Step 3b — `_recover_interrupted_agent_sessions_startup()`**: handles the remaining `running` sessions (those with a live PID or no PID yet) and transitions them back to `pending` so they are retried by the new worker.

### Local session recovery is `session_type`-aware (#1092)

A session whose `session_id` starts with `local` was spawned from a local Claude Code CLI rather than the Telegram bridge. Startup recovery handles these by `session_type` (the gate is `session_type == SessionType.ENG` at `agent/session_health.py:546`):

- **Local eng sessions** (`session_type == ENG`) are re-queued to `pending` just like bridge sessions. These are worker-owned child sessions spawned by a parent eng session via `valor-session create --role eng`, so no human CLI is competing for the same `claude_session_uuid`. The worker can safely resume the transcript on next pickup. This lets long-running eng-orchestrated pipelines (build + test + review + docs + merge) survive scheduled worker restarts on skills-only machines. When the recovered child finalizes, parent finalization flows through `finalize_session` → `_finalize_parent_sync` (in `models/session_lifecycle.py`), which transitions the parent through `waiting_for_children` to `completed`/`failed` once all children are terminal — no user-facing send callback is involved on this path.
- **Local Teammate sessions** continue to be abandoned. A live human CLI may hold the same `claude_session_uuid`; resuming would spawn a second harness competing at that UUID (the #986 hijack rationale).
- **`session_type == GRANITE` is a historical enum value only** (records predating the headless cutover, #1924). Nothing creates new Granite-typed sessions; the standalone CLI that used to (`valor-granite-loop`) no longer exists. Any surviving pre-cutover record falls through the same abandon path as Teammate.
- **Pre-migration records with `session_type == None`** fall through to the abandon path — a conservative default that also catches any future `SessionType` member added without explicit handling here (the gate uses explicit equality with `SessionType.ENG`).

### Summary

| Session state at restart | What happens |
|--------------------------|--------------|
| `pending` | Left untouched; new worker picks it up naturally |
| `running` (bridge) | Stays `running`; new worker startup re-queues it to `pending` |
| `running` (local `eng`) | Re-queued to `pending` (#1092); worker resumes via `claude --resume <UUID>` |
| `running` (local `teammate`/historical `granite`/pre-migration) | Finalized as `abandoned`; human CLI may reclaim |
| `complete` / `failed` / `killed` | Terminal — no action taken |

### Own-progress fields are heartbeat-gated (#1614)

The no-progress detector in `_has_progress` (`agent/session_health.py`) includes a set of "own-progress" fields — `turn_count`, `log_path`, and `claude_session_uuid` — that serve as evidence that a session authenticated with the SDK and began work. These fields are sticky once set and are only evaluated when `sdk_ever_output` is False, i.e. `agent.session_runner.liveness.derive_sdk_ever_output` returns False because none of `last_tool_use_at`, `last_turn_at`, or `last_stdout_at` has ever been written (issue #1935 added `last_stdout_at` as the third OR-input, closing the toolless-streaming false-positive window — see [Headless Session Runner § Liveness signals](headless-session-runner.md#liveness-signals-sdk_ever_output-issue-1935)).

**Confirmed Branch 2 failure mode (#1614):** The worker process remained alive (`worker_alive=True` on every health tick). The harness subprocess had exited or hung without producing SDK output, and the executor's heartbeat loop had silently stopped. But `claude_session_uuid` — written at SDK authentication time — was set. Because the own-progress check was ungated, it returned `True` unconditionally, blocking the branch-2 recovery path indefinitely.

### Reporting-layer taxonomies enumerate only headless-observed classes (#1926)

`agent/session_stall_classifier.py::classify_session_stall` and
`agent/crash_signature.py::extract_signature` are pure, zero-write, read-only
reporting modules that consume the same `derive_sdk_ever_output` /
`has_demonstrable_activity` leaves as the recovery paths above. Neither
imports `agent.session_health` (enforced by an import-isolation guard test).
As of #1926, both taxonomies enumerate only failure classes actually
observed post-headless-cutover (`never_started`, `idle_gap_exceeded_*`,
`status_transition`, plus the `ceiling`/`ceiling_timeout` prefix kept for
backward-compatible classification of pre-cutover rows) — every PTY-specific
class (`granite_wedged`, PTY-pool/granite-container/deadman signatures) was
already deleted by the #1930 teardown. See the [Removed Defenses
Ledger](../removed-defenses.md) for the full record of what was removed and
why, and [Stall Recovery](stall-recovery.md) for the action-mode gate ladder
that consumes the classifier's verdicts.

The `monitoring/worker_watchdog.py` W1-W5 kill ladder is unchanged by #1926
— it carries no PTY-specific text (its U-state rationale is the
substrate-agnostic issue-#1767 hung-worker recovery mechanism, not PTY
archaeology) — see [Bridge Self-Healing](bridge-self-healing.md).

**Fix:** the own-progress fields are now gated on `last_heartbeat_at` freshness using `NO_OUTPUT_BUDGET_SECONDS` (1800s). The gate logic:

- If `last_heartbeat_at` is within the last 1800s, own-progress fields are honoured as before.
- If `last_heartbeat_at` is absent or older than 1800s, the own-progress fields are skipped entirely and the session falls through to `_tier2_reprieve_signal` for Tier-2 evaluation.

This preserves the intended behaviour for sessions that are actively running (the heartbeat loop keeps `last_heartbeat_at` fresh) while allowing recovery of zombie sessions whose executor loop exited without finalizing the session.

**Gate window constraint:** the gate uses `NO_OUTPUT_BUDGET_SECONDS` (1800s), not the tighter `HEARTBEAT_FRESHNESS_WINDOW` (90s) — a session that is legitimately starting up and has `sdk_ever_output=False` must not be killed by the own-progress expiry before its executor genuinely gets a chance to boot and start producing output. (`NO_OUTPUT_BUDGET_SECONDS` is this gate's own window and the Tier-2 reprieve cap's; sub-check B's fresh-heartbeat fast-path is bounded separately by the D0 never-started gate at 150s, issue #1724 — #1905 pruned sub-check B's prior 1800s-scale grace-to-budget band as unreachable, so this gate's window is no longer a comparison point for sub-check B's bound.)

**Telemetry:** recoveries where `claude_session_uuid` is set but `sdk_ever_output` is False increment the `{project_key}:session-health:recoveries:zombie_uuid_no_output` Redis counter and emit a `[session-health] zombie_uuid_no_output recovery` log line.

For the full no-progress detector design see [Agent Session Health Monitor](agent-session-health-monitor.md#detection).

## Redis Communication Contract

The bridge and worker share a single contract: the `AgentSession` Popoto model in Redis.

| Field | Bridge writes | Worker reads |
|-------|--------------|-------------|
| `status` | `pending` (on enqueue) | Transitions: pending → running → complete/failed |
| `project_key` | Yes | Yes (routes to registered callbacks) |
| `chat_id` | Yes | Yes (computes `worker_key` for teammate sessions; slugged dev sessions use `slug` instead, PM and slugless dev use `project_key`) |
| `message_text` | Yes | Yes (passed to Claude) |
| `session_type` | Yes | Yes (PM/dev/teammate persona selection) |
| Redis steering list (`steering:{session_id}`, `agent/steering.py`) | Any process | Worker injects at turn boundary |

The bridge also reads `AgentSession.status` to determine if a session is already active (dedup logic).

### Context Prefix and Permission Guards

`build_context_prefix()` in `bridge/context.py` builds the permission restriction injected into the agent's first message. It uses `session_type` (not `is_dm`) as the authoritative signal:

- `session_type="teammate"` → injects read-only Teammate restriction
- `session_type="pm"` or `"dev"` → no restriction
- `session_type=None` → no restriction (e.g., catchup/reconciler paths)

In `agent/sdk_client.py`, `session_type` is resolved from Redis **before** `build_context_prefix()` is called, so the restriction decision is always based on the session's actual role, not on whether the message arrived via a DM channel.

## Per-session registry (`_active_sessions`)

`agent/session_state.py` (re-exported from `agent_session_queue.py`) maintains `_active_sessions: dict[str, SessionHandle]`,
a per-session registry keyed by `agent_session_id`. Each `SessionHandle` holds:

* `task` — the `asyncio.Task` currently running `_execute_agent_session`. The
  health check uses this to cancel wedged sessions in the kill path.
* `pid` — the SDK subprocess pid, populated by the messenger's
  `on_sdk_started` callback. Used by the two-tier detector's Tier 2
  process-alive / has-children gates.

**Lifecycle contract**:
* Single writer: `_execute_agent_session` for its own session id.
* Multi reader: `_agent_session_health_check`, `_tier2_reprieve_signal`.
* Registration happens at the very top of `_execute_agent_session`, **before**
  any raise site.
* Cleanup uses `asyncio.current_task().add_done_callback(...)` so the entry
  is popped even on exception, `CancelledError`, or early return.

See `verify anchor section exists`
for the full two-tier detector design.

## Messenger callbacks (ORM-free)

`BossMessenger` exposes three optional liveness callbacks, but only two are
wired at its construction site in `session_executor.py` (`agent/messenger.py`
still defines all three for contract-test purposes):

| Kwarg | Called from | Purpose |
|-------|-------------|---------|
| `on_sdk_started(pid)` | `_run_harness_subprocess` once the subprocess is spawned | Populate `SessionHandle.pid`; bump `last_sdk_heartbeat_at` |
| `on_heartbeat_tick()` | `BackgroundTask._watchdog` every 60s | Bump `last_sdk_heartbeat_at` |
| `on_stdout_event()` *(unwired, issue #1935)* | not called — `BossMessenger(...)` no longer passes this kwarg | N/A — `last_stdout_at` is written by `SessionRunner._stamp_stdout_liveness` in `agent/session_runner/runner.py` instead, wired directly into the headless driver's `on_stdout_event`/`on_init` adapters. See [Headless Session Runner § Liveness signals](headless-session-runner.md#liveness-signals-sdk_ever_output-issue-1935). |

All three callback slots are plumbed through `notify_*` wrappers that catch
callback exceptions and log at WARNING — the messenger is resilient to ORM
failures — but only the two wired kwargs above are exercised in production.
The messenger module imports nothing from `models/`; the queue layer
(`_execute_agent_session`) provides closures that do the ORM writes.

This keeps `agent/messenger.py` purely transport-layer while still surfacing
its existing liveness signals (the `SDK heartbeat: running Ns, communicated=...`
line) to the two-tier no-progress detector.

## Import Boundary

The bridge imports from `agent.agent_session_queue` are allowlisted to these functions only:
- `enqueue_agent_session` — enqueue new sessions
- `maybe_send_revival_prompt` — send a revival prompt to a dormant session
- `queue_revival_agent_session` — enqueue a revival session from a reply
- `cleanup_stale_branches` — clean up stale git branches on startup
- `register_callbacks` — register output delivery callbacks
- `clear_restart_flag` — clear stale update restart flag

Any function imported by the bridge that is not on this list is a violation of the boundary. The bridge does **not** import execution functions. If you see `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `_agent_session_health_loop`, `_session_notify_listener`, `_cleanup_orphaned_claude_processes`, `_reap_orphan_session_processes`, or `register_worker_pid` imported in `bridge/telegram_bridge.py`, that is a regression.

This boundary is enforced by `use correct Python path syntax`, which uses an allowlist to catch any unauthorized additions.

## Operator CLI

### Queue Status

```bash
python -m tools.agent_session_scheduler status
python -m tools.agent_session_scheduler list --status pending
python -m tools.agent_session_scheduler list --status pending --sort priority
python -m tools.agent_session_scheduler list --status pending,running --sort fifo
```

The `--sort` flag accepts: `priority` (by priority tier then FIFO), `fifo` (creation order), `status`. When sorting by `priority` or `fifo`, each pending session includes a `fifo_position` field showing its rank within its priority band.

### Session Management

```bash
# Bump a session to urgent priority and reset FIFO position
python -m tools.agent_session_scheduler bump --agent-session-id <ID>
python -m tools.agent_session_scheduler bump --agent-session-id <ID> --priority high

# Cancel a pending session
python -m tools.agent_session_scheduler cancel --agent-session-id <ID>

# Kill a running session
python -m tools.agent_session_scheduler kill --agent-session-id <ID>
python -m tools.agent_session_scheduler kill --all

# Clean up old terminal sessions
python -m tools.agent_session_scheduler cleanup --age 30 --dry-run
python -m tools.agent_session_scheduler cleanup --age 30
```

### Session Inspection (valor_session)

```bash
python -m tools.valor_session list                          # All sessions (shows priority column)
python -m tools.valor_session list --status pending         # Filter by status
python -m tools.valor_session status --id <ID>              # Full session details
python -m tools.valor_session steer --id <ID> --message "..." # Inject steering message
python -m tools.valor_session kill --id <ID>                # Kill a session
```

### Service Management

```bash
./scripts/valor-service.sh status          # Check both bridge and worker
./scripts/valor-service.sh restart         # Restart bridge, watchdog, and worker
./scripts/valor-service.sh worker-restart  # Restart worker only
./scripts/valor-service.sh worker-status   # Worker-specific status

# Worker stop semantics — choose deliberately:
./scripts/valor-service.sh worker-stop     # Transient stop (bootout only). launchd's
                                           # KeepAlive=true MAY respawn the worker. Use
                                           # for quick restart, debugging, etc.
./scripts/valor-service.sh worker-start    # Start the worker. Calls `launchctl enable`
                                           # idempotently first, so it correctly
                                           # recovers from a prior worker-disable.
./scripts/valor-service.sh worker-disable  # Stop the worker AND disable launchd
                                           # auto-respawn. Stays down until
                                           # worker-enable or worker-start. Use this
                                           # when you've killed all sessions and you
                                           # do NOT want the worker to come back.
./scripts/valor-service.sh worker-enable   # Re-enable launchd auto-respawn (does NOT
                                           # start the worker; pair with worker-start).
```

**`worker-stop` vs `worker-disable`**: `worker-stop` is intentionally transient — it does NOT touch launchd's enabled/disabled state, so the LaunchAgent's `KeepAlive=true` may auto-respawn the worker. This preserves backward compatibility for existing scripts and human muscle memory that relies on `worker-stop` being a one-shot stop. `worker-disable` is the explicit "stay down until I say otherwise" command, introduced in #1208 to fix the live-debug scenario where stopping the worker alone was insufficient because launchd kept respawning it.

## Update Orchestrator: Worker Start Verification

After installing the worker service during `/update`, the update orchestrator (`scripts/update/run.py`) verifies the worker actually starts:

1. **30-second heartbeat poll**: Checks `is_worker_running()` and heartbeat file mtime every 2 seconds (15 iterations).
2. **Kickstart fallback**: If no worker process is detected after 30 seconds, the orchestrator runs `verify command or replace {uid} with actual value reference` to force-start the service. This bypasses launchd's `ThrottleInterval` and handles cases where `bootout`+`bootstrap` registered the service but didn't start it.
3. **15-second re-poll**: After kickstart, polls for another 15 seconds (8 iterations) using the same heartbeat check.
4. **Error exit on persistent failure**: If the worker is still not running after the full 45-second window (30s initial + 15s post-kickstart), the update exits with `result.success = False` and logs `ERROR: Worker not running after kickstart retry -- system degraded`. This ensures operators are alerted to degraded state rather than silently continuing with a dead worker.

## Worker Exit Code and launchd Restart Behavior

The worker exits with **code 1** when shut down via SIGTERM (e.g., by `./scripts/valor-service.sh worker-restart`). This is intentional.

launchd's `ThrottleInterval` (configured at 10 seconds in `com.valor.worker.plist`) only applies to **non-zero exits**. A zero exit is treated as voluntary success and triggers launchd's internal ~10-minute default throttle, causing the worker to be unavailable for up to 10 minutes after a normal restart.

**How it works:**
- A module-level flag `_shutdown_via_signal` in `worker/__main__.py` is set to `True` only on SIGTERM.
- After `asyncio.run(_run_worker(...))` returns, `main()` checks the flag and calls `sys.exit(1)` if it is set.
- SIGINT (developer Ctrl-C) leaves the flag unset and exits 0 — a voluntary stop during development should not be penalized with a forced restart.
- `stop_worker()` in `scripts/valor-service.sh` uses `launchctl bootout` (the modern macOS API) to remove the worker from the launchd domain, consistent with `scripts/install_worker.sh`.
- `start_worker()` in `scripts/valor-service.sh` uses `launchctl bootout` (defensive, to clear any partial registration) followed by `launchctl bootstrap gui/<uid> <plist>` (issue #1407). This matches `stop_worker()` and `scripts/install_worker.sh`. Earlier code used `launchctl load`, which registered the service in a domain invisible to `gui/<uid>/` queries — this broke `KeepAlive` respawn and made the watchdog's recovery chain return rc=113. Bootstrap-based registration is the only path that keeps `KeepAlive` and `launchctl kickstart` working together. The bootstrap call itself now routes through `launchctl_bootstrap_fail_soft` (`scripts/lib/launchctl.sh`, issue #2013), which falls back to `launchctl kickstart -k` on a transient errno-5 bootstrap failure instead of aborting the install — see `docs/features/bridge-self-healing.md` Component 21.

**Result:** Worker killed via SIGTERM restarts within 15 seconds (10s `ThrottleInterval` + margin) rather than the ~10-minute default.

## Deployment Notes

Both the bridge and worker must run simultaneously for sessions to be executed. If only the bridge is running, sessions will queue in Redis but not be processed until the worker starts. The existing launchd watchdog (`com.valor.bridge-watchdog`) auto-restarts the bridge; a separate launchd service (`com.valor.worker`) auto-restarts the worker.

To verify both are running:

```bash
launchctl list | grep "valor"
```

Expected output:
```
<PID>  0  com.valor.bridge
<PID>  0  com.valor.worker
<PID>  0  com.valor.bridge-watchdog
```

## Telegram Relay Defect Fixes (issue #1749)

Four defects in `bridge/telegram_relay.py` and `bridge/dead_letters.py` were patched together because they share the same retry/dead-letter code path.

### 1. File-send idempotency (`_file_sent` flag)

When a message payload contains both a file and follow-up text, the relay sends them in two separate Telethon calls. If the file send succeeded but the process crashed before the text send completed, a naive retry would re-send the file, producing a duplicate attachment in the chat.

After a successful file send, the relay now writes `message["_file_sent"] = True` and `message["_file_msg_id"] = msg_id` directly onto the in-memory payload dict before proceeding to the text step. If the message is re-queued (because the text step failed), those keys survive serialisation into Redis. On the next dequeue, `_send_queued_message` checks `message.get("_file_sent")` and skips the `send_file` call, reusing the recorded `_file_msg_id`. This is the direct analogue of the `#1205` text-dedup guard applied to the file branch.

### 2. Oversized-text guard for file+text messages (`_maybe_send_oversized_text_as_file`)

The existing oversized-text detection (converting a >4096-char response to a `.txt` attachment) was only reachable from the text-only branch. The file+text branch called `send_message` directly on the follow-up text without length checking, so the guard was unreachable whenever a file was also present.

A shared helper `_maybe_send_oversized_text_as_file` was extracted. It accepts `(telegram_client, chat_id, text, reply_to, session_id)`, checks `len(text) > 4096`, converts to a temp `.txt` attachment if needed, and returns the resulting `msg_id` or `None` (no action taken). Both the file+text branch and the text-only branch now call this helper before their respective terminal sends (`send_file` → `send_message` vs. `send_markdown`). Each call site keeps its own terminal send for the normal-length case; the helper never sends normal-length text itself.

### 3. Group/supergroup-safe dead-letter (two guards narrowed in lockstep)

Group and supergroup Telegram chat IDs are legitimately negative integers. The previous guard `chat_id_int <= 0` rejected all negative IDs, silently discarding failed delivery records for group chats rather than persisting them to the dead-letter queue. The guard also appeared in the `replay_dead_letters` function in `bridge/dead_letters.py`, where it cleaned up any records already stored with negative `chat_id` values — so narrowing only the persist-side guard in `telegram_relay.py` would have been a no-op: the replay side would silently delete those records on next bridge startup.

Both guards were narrowed to `chat_id_int == 0` in lockstep:

- `_dead_letter_message` in `bridge/telegram_relay.py` (the persist side)
- `replay_dead_letters` in `bridge/dead_letters.py` (the replay side)

Only `chat_id=0` (an invalid Telegram peer that causes `PeerIdInvalidError` in a loop) is now excluded. Negative IDs are accepted and replayed normally.

### 4. Send-path FloodWait handling

`FloodWaitError` raised by Telethon during a relay send was previously uncaught inside `process_outbox`, propagating up to the outer `except Exception` handler and causing the message to be re-queued with a burned `_relay_attempts` counter. After three such failures the message was dead-lettered even though no actual delivery problem existed — the API was simply enforcing a rate limit.

`process_outbox` now catches `FloodWaitError` as a first-class case in the dispatch loop. On receipt it:

1. Sleeps `min(flood_err.seconds + RELAY_FLOOD_WAIT_BUFFER_SECS, RELAY_FLOOD_WAIT_MAX_SLEEP_SECS)`.
2. Increments `message["_flood_waits"]` (a separate counter, distinct from `_relay_attempts`).
3. Re-queues the message via `RPUSH` without touching `_relay_attempts`, carrying `_file_sent` if it was already set.
4. Dead-letters only after `RELAY_FLOOD_WAIT_MAX` consecutive flood events.

The three env-overridable constants (`RELAY_FLOOD_WAIT_BUFFER_SECS`, `RELAY_FLOOD_WAIT_MAX_SLEEP_SECS`, `RELAY_FLOOD_WAIT_MAX`) are marked provisional — tune from production telemetry.

This handler is distinct from the `FloodWaitError` handler in `telegram_bridge.py`'s connect loop. The connect-loop handler also calls `_write_flood_backoff` to throttle reconnects; the relay send-path handler deliberately omits that side-effect because the relay is not re-establishing a connection.

## Background: Prior Separation Efforts

| Effort | What It Did | Why It Was Incomplete |
|--------|-------------|----------------------|
| PR #737 | Created `worker/__main__.py`, moved session execution there | Did not remove execution imports from bridge; bridge still called `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `rebuild_indexes` at startup |
| Issue #741 | Added graceful shutdown and persistent event loop to worker | Addressed worker robustness only; bridge coupling was out of scope |
| Issue #750 | Enforced the import boundary: removed all execution calls from bridge, consolidated full startup sequence in worker | Complete separation achieved |

The root cause of prior incompleteness: each effort treated the worker as additive — creating worker capability without stripping bridge capability. This issue enforced the boundary at the import level.
