# Session Steering

**Scope:** Turn-boundary inbox (the Redis steering list, `agent/steering.py`) consumed by the worker executor. Used by `valor-session steer` and `scripts/steer_child.py`.

**See also:**
- [Mid-Session Steering](mid-session-steering.md) — Telegram reply-thread flow (user-facing)
- [Steering Queue: Historical Spec](steering-implementation-spec.md) — Original Redis list design and bridge coalescing
- [PM Final Delivery](pm-final-delivery.md) — SDLC terminal-turn protocol. Fan-out completion invokes the completion-turn runner directly; it does not go through the steering inbox. The `[PIPELINE_COMPLETE]` content marker historically referenced in earlier docs was retired in issue #1058.

External steering for `AgentSession` via the Redis steering list. Any process — the PM, a CLI user, another agent — can write messages to a running session's inbox. The worker injects them at the next turn boundary.

## Problem

Before this feature, the only steering mechanism was the hardcoded nudge loop inside `agent/agent_session_queue.py`. The executor decided whether to continue or stop, PM-specific logic was embedded in the generic executor, and there was no external way to say "stop after this stage."

## Architecture

### Steering Inbox

The Redis list at key `steering:{session_id}` (`agent/steering.py`) is the sole steering inbox. Any process can write to it; the worker consumes at turn boundaries.

- **Cap**: none enforced at the queue level; the worker drains the full list each turn boundary
- **Storage**: Redis List (`RPUSH`/`LPUSH` to add, `LPOP` to consume), JSON-encoded entries
- **Atomicity**: Write via `push_steering_message(session_id, text, sender, ...)`, drain via `pop_all_steering_messages(session_id)` (sequential atomic `LPOP`s), or peek non-destructively via `peek_steering_messages(session_id)`

### Turn Boundary Check

At the start of each agent turn in `_execute_agent_session()` (`agent/session_executor.py`), the worker drains the session's Redis steering list via `pop_all_steering_messages()`. If messages are pending, the first is used as the user input for that turn (replacing the original message text). Remaining messages are re-pushed onto the list for future turns.

```python
# Inside _execute_agent_session(), before do_work():
from agent.steering import pop_all_steering_messages, push_steering_message

steering_msgs = pop_all_steering_messages(session.session_id)
if steering_msgs:
    _turn_input = steering_msgs[0]["text"]
    # Re-push remaining messages for future turns
    for msg in steering_msgs[1:]:
        push_steering_message(
            session.session_id, msg["text"], msg["sender"], is_abort=msg.get("is_abort", False)
        )
```

### Output Router

`agent/output_router.py` contains the extracted routing logic:

- `determine_delivery_action()` — pure function, returns action string. Accepts an optional `last_compaction_ts: float | None`; when set and within `POST_COMPACT_NUDGE_GUARD_SECONDS = 30` of now, short-circuits to the `"defer_post_compact"` action. See [Compaction Hardening](compaction-hardening.md) (issue #1127).
- `route_session_output()` — wraps above with persona-aware nudge cap; forwards `last_compaction_ts` through to the pure function.
- `MAX_NUDGE_COUNT`, `NUDGE_MESSAGE`, `SendToChatResult` — constants and dataclass

The `send_to_chat()` callback in the executor calls `route_session_output()` and executes the returned action. The call site stays inside `send_to_chat()` to preserve temporal coupling with `chat_state` flag-setting and post-execution cleanup. The `"defer_post_compact"` branch is a pure no-op — no nudge enqueue, no `completion_sent` flip, no `auto_continue_count` bump — so the next SDK tick naturally re-evaluates routing.

### Public Steering API

`agent/session_executor.py` exports:

- `steer_session(session_id, message)` — pushes to the Redis steering list via `agent.steering.push_steering_message()`, validates non-terminal status, wakes worker
- `re_enqueue_session(session, ...)` — public wrapper for `_enqueue_nudge`, encapsulates re-enqueue logic

## Data Flow

```
External caller (CLI, PM, agent)
  → steer_session(session_id, "Stop after critique")
    → push_steering_message()               [Redis RPUSH]
    → _ensure_worker()                      [wake worker]

Worker loop
  → _execute_agent_session()
    → pop_all_steering_messages()           [Redis LPOP drain]
    → if messages: use first as turn input, re-push the rest
    → get_response_via_harness(_turn_input, ...)  [agent/session_runner/harness/claude.py]
    → send_to_chat() callback
      → route_session_output()              [output_router.py]
        → determine_delivery_action()       [pure function]
      → execute action (deliver/nudge/drop)
```

## CLI: valor-session

`tools/valor_session.py` — session management tool modeled after `valor-telegram`.

```bash
# Create a new session (project_key derived from cwd via projects.json)
# The eng role requires --slug, or `issue #N` in the message for auto-derivation.
valor-session create --role eng --message "Plan issue #735"
valor-session create --role eng --slug fix-the-bug --message "Fix the bug"
valor-session create --role eng --slug ad-hoc-task --message "..." --project-key valor  # explicit override

# Steer a running session
valor-session steer --id abc123 --message "Stop after critique stage"

# Inspect session state
valor-session status --id abc123

# List sessions
valor-session list
valor-session list --status running
valor-session list --role eng

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

When the drafter detects a blocking flag (empty promise / forward-deferral without evidence, or — since issue #1955 — any non-empty wire-format `Violation` list, e.g. a markdown table or a local file-path reference), `_inject_self_draft_steering(session, draft)` in `agent/output_handler.py` uses the steering infrastructure to request a self-draft from the authoring agent rather than delivering a bad message to the user. When the deferred draft carries a `local_file_path_reference` violation, the pushed instruction gets a targeted addendum directing the agent to attach the file via `tools/send_message.py "<caption>" --file <path>` instead of re-pasting the path.

This is the **primary** flag-handling path — not a fallback. The drafter no longer calls Haiku or OpenRouter; the steering nudge is how the system handles any output that fails validation.

**Mechanism:** `push_steering_message(session_id, SELF_DRAFT_INSTRUCTION, sender="drafter-fallback")` injects a compact self-draft instruction. The agent produces a clean draft on its next turn. (The `sender="drafter-fallback"` string supersedes the older `"summarizer-fallback"` string used before the drafter rewrite.)

**Loop prevention:** `peek_steering_sender(session_id)` checks if a `"drafter-fallback"` message is already queued before pushing another, blocking double-injection if the agent hasn't yet consumed the prior steering message. Additionally, the attempt count is tracked atomically at `steering:attempts:{session_id}` in Redis (`bump_self_draft_attempts`, `reset_self_draft_attempts` in `agent/steering.py`).

**Attempt cap:** `SELF_DRAFT_MAX_ATTEMPTS = 2` (in `agent/steering.py`). After two consecutive steering injections without a clean delivery in between, the handler falls through to the narration fallback rather than injecting a third message. The counter resets on any clean (non-blocking) delivery via `reset_self_draft_attempts`.

**Fallback chain:** If steering cannot be used (no session, Redis down, cap exceeded), the system falls through to the narration fallback — a fixed message substituted in place of the agent's raw output.

See [Message Drafter](message-drafter.md) for the current feature doc covering the drafter module. (The previous pointer to `summarizer-format.md` is gone — content migrated into `message-drafter.md`.)

### Terminal-Path Re-enqueue Suppression (issue #1794 / #2197)

A **terminal-turn** self-draft deferral (the agent's last turn before reaching a
terminal status defers a raw reply for self-draft, and no clean redraft ever
lands) is delivered by a **single** handler: the completed-path flush —
`agent.session_health.flush_deferred_self_draft_sync` on the telegram sync path,
or `_deliver_deferred_self_draft_fallback` on the email async path — flushes the
held `deferred_self_draft_text` to the human exactly once (see issue #1794).

A second, independent handler used to fire uncoordinated with the flush:
`_execute_agent_session`'s steering-queue cleanup block in
`agent/session_executor.py` re-enqueues any unconsumed steering messages as a
continuation session via `enqueue_agent_session` (which has no
`claude_session_uuid` param, so it spawns a brand-new, context-blind session).
Because the flush does not pop the steering queue, the still-present
`drafter-fallback` steering message was popped and re-enqueued too — the new
session, told to "rewrite it" with no "it" to rewrite, took the
`SELF_DRAFT_INSTRUCTION` escape hatch ("If your work produced no substantive
results, say so plainly") and emitted a misleading "no substantive results"
reply, even though the prior turn had produced real, correct output.

**Fix:** the re-enqueue block (extracted into
`_reenqueue_leftover_steering(session, agent_session, working_dir, leftover)` in
`agent/session_executor.py`) partitions leftover steering by sender before
re-enqueueing:

- Messages with `sender == DRAFTER_FALLBACK_SENDER` (`agent/output_handler.py`)
  are dropped — the terminal delivery flush already owns delivering that
  content exactly once.
- Every other sender (including a leftover message with a missing/`None`
  `sender`) re-enqueues exactly as before.

The suppression is **transport-agnostic**: it applies regardless of whether the
session originated on telegram or email, since the partition lives in the
shared re-enqueue path, not in either transport-specific flush.

## Watchdog-Authored Steering (issue #1128)

The session watchdog (`monitoring/session_watchdog.py`) is now an active
steering-message **sender** alongside humans, parent Eng sessions, and the
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

## Automatic Steering on tool_timeout Recovery

When the per-tool timeout sub-loop (mechanism 10 in `session-recovery-mechanisms.md`) detects a session wedged on a hung MCP or other tool call, `_apply_recovery_transition` handles the `tool_timeout` recovery kind. On the requeue (`pending`) branch — i.e., before `MAX_RECOVERY_ATTEMPTS` is exhausted — the recovery helper now automatically prepends a skip-the-tool steering message to the session's inbox.

### How it works

1. `_compose_tool_timeout_steering(tool_name, original_message)` builds a self-contained message that:
   - Names the specific tool that timed out
   - Embeds the user's original request verbatim
   - Instructs the model to skip the hung tool and answer using available context

2. `push_steering_message(entry.session_id, ..., front=True)` prepends the message at index 0 of the Redis steering list.

3. On re-pickup, the worker's turn-boundary drain pops this message first and uses it as the turn input.

### Why prepend, not append

The Redis steering list is a FIFO queue. Any previously queued messages would run first if the tool-skip instruction were appended. Prepending via `front=True` ensures the skip instruction is the first thing the model sees on re-pickup, before any human-authored or watchdog-authored messages that were already in the queue.

The `front=True` parameter on `push_steering_message` trims from the back of the list (preserving the new message at index 0 and the oldest existing messages). The default `front=False` behavior (append, trim from head) is unchanged.

### Self-containedness on requeue

The steering message is self-contained regardless: it embeds the original
request text so the model can answer even if the re-queued turn resumes
without direct memory of the prior turn (e.g. a cold-start fallback after a
stale/invalid resume scalar — see [Headless Session
Runner](headless-session-runner.md#simple-resume-d3-four-scalars)). On the
common path the runner resumes the same Claude session via the persisted
`claude_session_uuid`, so the model also has its own transcript; the embedded
original-request text is a belt-and-suspenders floor, not the only memory.

### Deterministic floor on terminal failure

On the terminal `failed` branch — both the `MAX_RECOVERY_ATTEMPTS` exhaustion path and the not-confirmed-dead path — `_deliver_tool_timeout_degraded_notice(entry, tool_name)` delivers a canned user-facing message through the session's resolved output handler. This is the **deterministic floor**: even if advisory steering was never injected (e.g. the session failed on its first attempt), the user still receives a reply.

- **Idempotency**: Redis `SETNX` on a per-session key prevents double-delivery if both failure branches fire in a race.
- **Channel-agnostic**: `_resolve_callbacks(project_key, transport)` routes the message through Telegram, email, or file output — whichever transport the session was using.

See [Session Recovery Mechanisms §Per-Tool Timeout Sub-Loop](session-recovery-mechanisms.md#10-per-tool-timeout-sub-loop-_agent_session_tool_timeout_loop) for the recovery trigger conditions and tier budgets.

## Mid-Turn Steering: Auto-Preempt (D4, issue #1924) — supersedes the wedge-nudge channel

The turn-boundary drain above only reaches a session between turns. Under the
prior PTY substrate that meant a mid-turn steer targeting a wedged session had
no way in short of a separate signal channel (the now-deleted wedge-nudge
mechanism, issue #1879). The [headless session runner](headless-session-runner.md)
closes that gap structurally instead of adding a second channel: **any**
steering message arriving mid-turn auto-preempts the in-flight turn.

A per-turn watcher polls the ordinary `steering:{session_id}` list while a
turn is running. On a substantive steer (after a short debounce window that
batches steers arriving within a few seconds into one preempt) it terminates
the turn's own process group — SIGTERM, a bounded grace window for the CLI to
flush its transcript, then SIGKILL if needed. The kill is
generation-token-guarded: the watcher only acts if the turn it captured at
spawn time is still the current one, so a steer landing just as a turn
finishes naturally can never kill the *next* turn. The next turn `--resume`s
with the steer injected as its first message, and the partial prior turn's
transcript is preserved (never silently discarded).

A per-turn timeout follows the identical path (`turn_end_source="timeout"`
instead of `"preempted"`) — expiry is a graceful preempt, not an error, so a
long Dev build is never dropped by its own ceiling.

There is exactly one steering channel (`steering:{session_id}`); operator
steering via `valor-session steer` needs no special-casing for mid-turn vs.
turn-boundary delivery — the runner's preempt watcher makes turn-boundary
delivery the only shape steering ever needs to reason about.

## Parent-Child Steering (parent Eng session to child Eng session)

In addition to Telegram reply-thread steering (user to agent), the steering queue supports **parent-child steering**. Parent and child sessions are both Eng sessions (`session_type="eng"`); a parent created its child via `AgentSession.create_child()` for parallel work, and steers it while it runs.

### How It Works

The parent Eng session invokes `scripts/steer_child.py` via bash to push steering messages to a running child Eng session. The script validates the parent-child relationship, then routes through one of two paths depending on whether the message is an abort.

```
Parent Eng session decides to steer
    |
    v
python scripts/steer_child.py --session-id <child_id> --message "focus on tests" --parent-id <parent_id>
    |
    v
Script validates: child exists, is an Eng session (is_eng), parent_agent_session_id matches, status is "running"
    |
    +-- non-abort --> steer_session(session_id, message)
    |                   → Redis steering list (turn-boundary inbox)
    |                   → child consumes at its next turn boundary
    |
    +-- --abort -----> push_steering_message(session_id, text, sender="pm", is_abort=True)
                        → Redis abort queue; the watchdog hook delivers immediately
                          via additionalContext injection
```

The non-abort path uses the turn-boundary inbox (the Redis steering list) via `steer_session()`, which works for both SDK-harness and CLI-harness sessions. The abort path uses the same Redis list (`push_steering_message(..., is_abort=True)`) so the watchdog hook can deliver the stop signal immediately rather than waiting for a turn boundary.

### CLI Usage

```bash
# Steer a running child Eng session
python scripts/steer_child.py --session-id <child_id> --message "skip docs, focus on tests" --parent-id <parent_id>

# Send abort signal to a child
python scripts/steer_child.py --session-id <child_id> --message "stop" --parent-id <parent_id> --abort

# List active child Eng sessions
python scripts/steer_child.py --list --parent-id <parent_id>
```

The `--parent-id` can also be read from the `VALOR_SESSION_ID` environment variable, which is set by `sdk_client.py` for running sessions.

### Validation

The script enforces strict parent-child relationship validation in `_steer_child()`:

- Target must be an existing AgentSession
- Target must be an Eng session (`child.is_eng` check)
- Target's `parent_agent_session_id` must match the caller's parent ID
- Target must be in `running` status

All validation failures exit with non-zero code and print an error to stderr.

### Relationship to Bridge Steering

| Aspect | Bridge Steering | Parent-Child Steering |
|--------|----------------|----------------------|
| Caller | Telegram user (via reply thread) | Parent Eng session (via bash script) |
| Entry point | `bridge/telegram_bridge.py` | `scripts/steer_child.py` |
| Validation | Session ID match + running status | Parent-child relationship (`is_eng` + `parent_agent_session_id`) + running status |
| Non-abort path | Turn-boundary inbox (Redis steering list) | Turn-boundary inbox (Redis steering list) via `steer_session()` |
| Abort path | n/a | Redis abort queue via `push_steering_message(..., is_abort=True)` |
| Sender field | User's name | `"pm"` (abort path) |

The non-abort path converges on `steer_session()` in `agent/session_executor.py` and the same turn-boundary inbox documented above. The abort path uses `push_steering_message()` in `agent/steering.py` with `sender="pm"`.

## No-Gos

- `bridge/message_drafter.py` `nudge_feedback` is untouched — separate concept
- The `send_to_chat()` call site remains mid-execution (not post-execution)
- `OutputHandler` protocol is unchanged
- No web UI — CLI only
