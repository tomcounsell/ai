# Session Steering

**Scope:** Turn-boundary inbox (`AgentSession.queued_steering_messages`) consumed by the worker executor. Used by `valor-session steer` and `scripts/steer_child.py`.

**See also:**
- [Mid-Session Steering](mid-session-steering.md) — Telegram reply-thread flow (user-facing)
- [Steering Queue: Historical Spec](steering-implementation-spec.md) — Original Redis list design and bridge coalescing
- [PM Final Delivery](pm-final-delivery.md) — SDLC terminal-turn protocol. Fan-out completion invokes the completion-turn runner directly; it does not go through the steering inbox. The `[PIPELINE_COMPLETE]` content marker historically referenced in earlier docs was retired in issue #1058.

External steering for `AgentSession` via `queued_steering_messages`. Any process — the PM, a CLI user, another agent — can write messages to a running session's inbox. The worker injects them at the next turn boundary.

## Problem

Before this feature, the only steering mechanism was the hardcoded nudge loop inside `agent/agent_session_queue.py`. The executor decided whether to continue or stop, PM-specific logic was embedded in the generic executor, and there was no external way to say "stop after this stage."

## Architecture

### Steering Inbox

`AgentSession.queued_steering_messages` (a `ListField`, already on the model) is the canonical steering inbox. Any process can write to it; the worker consumes at turn boundaries.

- **Cap**: `STEERING_QUEUE_MAX = 10` messages per session
- **Storage**: Popoto `ListField` persisted in Redis
- **Atomicity**: Write via `push_steering_message()`, read via `pop_steering_messages()`

### Turn Boundary Check

At the start of each agent turn in `_execute_agent_session()`, the worker checks the session's `queued_steering_messages`. If messages are pending, the first is popped and used as the user input for that turn (replacing the original message text). Remaining messages are re-queued for future turns.

```python
# Inside _execute_agent_session(), before do_work():
steering_msgs = agent_session.pop_steering_messages()
if steering_msgs:
    _turn_input = steering_msgs[0]
    # Re-queue remaining for future turns
    for msg in steering_msgs[1:]:
        agent_session.push_steering_message(msg)
```

### Output Router

`agent/output_router.py` contains the extracted routing logic:

- `determine_delivery_action()` — pure function, returns action string. Accepts an optional `last_compaction_ts: float | None`; when set and within `POST_COMPACT_NUDGE_GUARD_SECONDS = 30` of now, short-circuits to the `"defer_post_compact"` action. See [Compaction Hardening](compaction-hardening.md) (issue #1127).
- `route_session_output()` — wraps above with persona-aware nudge cap; forwards `last_compaction_ts` through to the pure function.
- `MAX_NUDGE_COUNT`, `NUDGE_MESSAGE`, `SendToChatResult` — constants and dataclass

The `send_to_chat()` callback in the executor calls `route_session_output()` and executes the returned action. The call site stays inside `send_to_chat()` to preserve temporal coupling with `chat_state` flag-setting and post-execution cleanup. The `"defer_post_compact"` branch is a pure no-op — no nudge enqueue, no `completion_sent` flip, no `auto_continue_count` bump — so the next SDK tick naturally re-evaluates routing.

### Public Steering API

`agent/agent_session_queue.py` exports:

- `steer_session(session_id, message)` — writes to `queued_steering_messages`, validates non-terminal status, wakes worker
- `re_enqueue_session(session, ...)` — public wrapper for `_enqueue_nudge`, encapsulates re-enqueue logic

## Data Flow

```
External caller (CLI, PM, agent)
  → steer_session(session_id, "Stop after critique")
    → AgentSession.push_steering_message()  [Redis write]
    → _ensure_worker()                      [wake worker]

Worker loop
  → _execute_agent_session()
    → agent_session.pop_steering_messages() [Redis read]
    → if messages: use first as turn input
    → get_agent_response_sdk(_turn_input, ...)
    → send_to_chat() callback
      → route_session_output()              [output_router.py]
        → determine_delivery_action()       [pure function]
      → execute action (deliver/nudge/drop)
```

## CLI: valor-session

`tools/valor_session.py` — session management tool modeled after `valor-telegram`.

```bash
# Create a new session (project_key derived from cwd via projects.json)
# PM and dev roles both require --slug, or `issue #N` in the message for auto-derivation.
valor-session create --role pm --message "Plan issue #735"
valor-session create --role dev --slug fix-the-bug --message "Fix the bug" --parent abc123
valor-session create --role pm --slug ad-hoc-task --message "..." --project-key valor  # explicit override

# Steer a running session
valor-session steer --id abc123 --message "Stop after critique stage"

# Inspect session state
valor-session status --id abc123

# List sessions
valor-session list
valor-session list --status running
valor-session list --role pm

# Kill sessions
valor-session kill --id abc123
valor-session kill --all
```

Add `--json` to any command for machine-readable output.

### Worker pre-flight check (`create` and `status`)

After enqueuing a session, `valor-session create` checks whether a worker is actively writing heartbeats. `valor-session status` runs the same check when the session is in `pending` status.

**States:**

- `ok` (heartbeat age < 600s) — silent; no output
- `down` (heartbeat age ≥ 600s, or file missing) — warning to stderr:
  ```
  WARNING: no recent worker heartbeat on this machine (720s) — session will stay pending until a worker is started (run: ./scripts/valor-service.sh worker-start)
  ```

The warning never claims the session will not run — it reports the current heartbeat age so the operator can judge. The session is enqueued regardless; when a worker starts, it picks up pending sessions normally.

**Threshold:** `WORKER_DOWN_THRESHOLD_S = 600s` (defined in `agent/constants.py`), 2x the worker's 300s heartbeat write cadence. This gives one full missed write cycle of margin before declaring the worker down.

**Worktree-proof path resolution:** The heartbeat file lives at `data/last_worker_connected` in the main checkout. When the CLI runs from a git worktree (`.worktrees/{slug}/`), a naive `__file__`-relative path would point at the worktree's own `data/` dir, which the worker never writes. `_resolve_heartbeat_path()` uses `git rev-parse --path-format=absolute --git-common-dir` (flag order matters — `--path-format=absolute` must precede `--git-common-dir`) to locate the main checkout's `.git` dir and derive the canonical heartbeat path. Relative git output is resolved against the `__file__` anchor, never the process cwd. Any git subprocess failure falls back to the `__file__`-relative path silently.

**JSON contract:** `--json` output always includes these fields:

| Field | Type | Meaning |
|-------|------|---------|
| `worker_state` | `"ok"` or `"down"` | Structured state for agent callers — always present |
| `worker_heartbeat_age_s` | integer or `null` | Age of the heartbeat file in seconds; `null` if file is missing; clamped to ≥ 0 |
| `worker_healthy` | boolean | `true` when `worker_state == "ok"` — kept for backward compatibility |

In `cmd_create`, all three fields are always non-null. In `cmd_status`, `worker_state` and `worker_heartbeat_age_s` are unconditionally present but are `null` when the session is not `pending` (the compute is skipped to avoid running the git subprocess on every status call). `worker_healthy` only appears in `cmd_status` JSON when the session is pending.

Agent callers should branch on `worker_state` rather than parsing the stderr warning text.

## Backward Compatibility

All symbols that previously lived only in `agent/agent_session_queue.py` are re-exported from there for backward compatibility:

- `MAX_NUDGE_COUNT`
- `NUDGE_MESSAGE`
- `SendToChatResult`
- `determine_delivery_action`

Existing callers (tests, integrations) that import from `agent.agent_session_queue` continue to work unchanged. The canonical location is now `agent.output_router`.

## Drafter Self-Draft Steering (née "Summarizer Fallback")

When the drafter detects a blocking flag (empty promise / forward-deferral without evidence), `_inject_self_draft_steering()` in `agent/output_handler.py` uses the steering infrastructure to request a self-draft from the authoring agent rather than delivering a bad message to the user.

This is the **primary** flag-handling path — not a fallback. The drafter no longer calls Haiku or OpenRouter; the steering nudge is how the system handles any output that fails validation.

**Mechanism:** `push_steering_message(session_id, SELF_DRAFT_INSTRUCTION, sender="drafter-fallback")` injects a compact self-draft instruction. The agent produces a clean draft on its next turn. (The `sender="drafter-fallback"` string supersedes the older `"summarizer-fallback"` string used before the drafter rewrite.)

**Loop prevention:** `peek_steering_sender(session_id)` checks if a `"drafter-fallback"` message is already queued before pushing another, blocking double-injection if the agent hasn't yet consumed the prior steering message. Additionally, the attempt count is tracked atomically at `steering:attempts:{session_id}` in Redis (`bump_self_draft_attempts`, `reset_self_draft_attempts` in `agent/steering.py`).

**Attempt cap:** `SELF_DRAFT_MAX_ATTEMPTS = 2` (in `agent/steering.py`). After two consecutive steering injections without a clean delivery in between, the handler falls through to the narration fallback rather than injecting a third message. The counter resets on any clean (non-blocking) delivery via `reset_self_draft_attempts`.

**Fallback chain:** If steering cannot be used (no session, Redis down, cap exceeded), the system falls through to the narration fallback — a fixed message substituted in place of the agent's raw output.

See [Message Drafter](message-drafter.md) for the current feature doc covering the drafter module. (The previous pointer to `summarizer-format.md` is gone — content migrated into `message-drafter.md`.)

## Watchdog-Authored Steering (issue #1128)

The session watchdog (`monitoring/session_watchdog.py`) is now an active
steering-message **sender** alongside humans, the PM session, and the
drafter fallback. When one of three conditions fires, the watchdog
enqueues a targeted message via
`_inject_watchdog_steer(session_id, reason, message)`, which calls
`push_steering_message(..., sender="watchdog")`:

| Reason | Trigger | Message template |
|--------|---------|-------------------|
| `repetition` | `detect_repetition` returns True | "Stop and re-check the task — you appear to be repeating the same tool call..." |
| `error_cascade` | `detect_error_cascade` returns True | "Stop — you've hit N errors in the last 20 operations..." |
| `token_alert` | cumulative `input+output` tokens ≥ `TOKEN_ALERT_THRESHOLD` on a `running` session | "Token budget exceeded: $X / Y tokens spent this session..." |

**Sender='watchdog'** lets downstream consumers distinguish automated
nudges from human steers:

- `valor-session status --id <id>` renders the sender on each queued entry.
- The dashboard JSON includes `sender` on queued-steering entries.
- `agent/session_executor.py`'s steering-drain loop logs `[steering]
  received from sender=watchdog` so operators can trace which ticks
  corresponded to a watchdog-driven correction.

**Per-reason atomic cooldown.** Redis `SET key "1" NX EX <ttl>` with a
reason-scoped key (`watchdog:steer_cooldown:<reason>:<session_id>`)
eliminates the read-then-write race entirely. A `repetition` steer does
not suppress a parallel `error_cascade` or `token_alert` steer.

**Feature gate.** `WATCHDOG_AUTO_STEER_ENABLED=false` disables the push
without disabling the detection (still logged at WARNING).

## Parent-Child Steering (PM session to Dev session)

In addition to Telegram reply-thread steering (user to agent), the steering queue supports **parent-child steering** where a PM session (PM persona) pushes steering messages to its spawned Dev sessions.

### How It Works

The PM session invokes `scripts/steer_child.py` via bash to push steering messages to a running child Dev session. The script validates the parent-child relationship before pushing to the same Redis steering queue used by bridge steering.

```
PM session decides to steer
    |
    v
python scripts/steer_child.py --session-id <child_id> --message "focus on tests" --parent-id <parent_id>
    |
    v
Script validates: child exists, is a Dev session, parent_agent_session_id matches, status is "running"
    |
    v
push_steering_message(child_session_id, text, sender="PM session")
    |
    v
Child's watchdog picks up on next tool call (existing _handle_steering in health_check.py)
    |
    v
Dev session adjusts behavior
```

### CLI Usage

```bash
# Steer a child Dev session
python scripts/steer_child.py --session-id <child_id> --message "skip docs, focus on tests" --parent-id <parent_id>

# Send abort signal to a child
python scripts/steer_child.py --session-id <child_id> --message "stop" --parent-id <parent_id> --abort

# List active child Dev sessions
python scripts/steer_child.py --list --parent-id <parent_id>
```

The `--parent-id` can also be read from the `VALOR_SESSION_ID` environment variable, which is set by `sdk_client.py` for running sessions.

### Validation

The script enforces strict parent-child relationship validation:

- Target must be an existing AgentSession
- Target must be a Dev session (`is_dev` check)
- Target's `parent_agent_session_id` must match the caller's ID
- Target must be in `running` status

All validation failures exit with non-zero code and print an error to stderr.

### Relationship to Bridge Steering

| Aspect | Bridge Steering | Parent-Child Steering |
|--------|----------------|----------------------|
| Caller | Telegram user (via reply thread) | PM session (via bash script) |
| Entry point | `bridge/telegram_bridge.py` | `scripts/steer_child.py` |
| Validation | Session ID match + running status | Parent-child relationship + running status |
| Redis queue | Same (`steering:{session_id}`) | Same (`steering:{session_id}`) |
| Consumption | Same (watchdog `_handle_steering`) | Same (watchdog `_handle_steering`) |
| Sender field | User's name | "PM session" |

Both paths converge on the same `push_steering_message()` function in `agent/steering.py` and the same consumption path in the watchdog hook.

## No-Gos

- `bridge/message_drafter.py` `nudge_feedback` is untouched — separate concept
- The `send_to_chat()` call site remains mid-execution (not post-execution)
- `OutputHandler` protocol is unchanged
- No web UI — CLI only
