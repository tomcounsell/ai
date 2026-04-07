# Session Steering

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

- `determine_delivery_action()` — pure function, returns action string
- `route_session_output()` — wraps above with persona-aware nudge cap
- `MAX_NUDGE_COUNT`, `NUDGE_MESSAGE`, `SendToChatResult` — constants and dataclass

The `send_to_chat()` callback in the executor calls `route_session_output()` and executes the returned action. The call site stays inside `send_to_chat()` to preserve temporal coupling with `chat_state` flag-setting and post-execution cleanup.

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
# Create a new session
valor-session create --role pm --message "Plan issue #735"
valor-session create --role dev --message "Fix the bug" --parent abc123

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

## Backward Compatibility

All symbols that previously lived only in `agent/agent_session_queue.py` are re-exported from there for backward compatibility:

- `MAX_NUDGE_COUNT`
- `NUDGE_MESSAGE`
- `SendToChatResult`
- `determine_delivery_action`

Existing callers (tests, integrations) that import from `agent.agent_session_queue` continue to work unchanged. The canonical location is now `agent.output_router`.

## No-Gos

- `bridge/summarizer.py` `nudge_feedback` is untouched — separate concept
- The `send_to_chat()` call site remains mid-execution (not post-execution)
- `OutputHandler` protocol is unchanged
- No web UI — CLI only
