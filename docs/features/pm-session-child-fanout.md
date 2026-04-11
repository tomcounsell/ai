# PM Session Child Fan-out

## Overview

When a PM session receives a message containing multiple GitHub issue numbers (e.g., "Run SDLC on issues 777, 775, 776"), it fans out: instead of handling all issues in a single session with growing context, it spawns one child PM session per issue and pauses itself until all children complete.

## Trigger Conditions

Fan-out activates when:
- The PM session's message text contains **more than one GitHub issue number** requiring SDLC work.
- Examples: "Run SDLC on issues 777, 775, 776", "Process #777 and #775", "777, 775, 776".

Fan-out does **not** activate for status queries (e.g., "what's the status of 777 and 775?") — the PM answers those directly.

## Data Flow

1. **Parent PM session** receives the multi-issue message.
2. **Fan-out**: PM runs one `valor_session create --role pm` call per issue:
   ```bash
   python -m tools.valor_session create \
     --role pm \
     --parent "$AGENT_SESSION_ID" \
     --message "Run SDLC on issue 777"
   ```
3. **Pause**: After spawning all children, PM calls:
   ```bash
   python -m tools.valor_session wait-for-children --session-id "$AGENT_SESSION_ID"
   ```
   This transitions the parent to `waiting_for_children`.
4. **Telegram update**: PM sends a visibility message before pausing (e.g., "Spawning 3 child sessions for issues 777, 775, 776 — I'll pause until all complete.").
5. **Child execution**: The worker picks up each child PM session via project-keyed serialization (one at a time). Each child runs its own isolated SDLC pipeline.
6. **Auto-completion**: When each child reaches a terminal state, `_finalize_parent_sync()` in `models/session_lifecycle.py` fires. When all children are terminal, the parent auto-transitions to `completed`.

## Key Components

### `tools/valor_session.py` — `wait-for-children` Subcommand

New subcommand added to the `valor-session` CLI:

```
python -m tools.valor_session wait-for-children [--session-id SESSION_ID]
```

- `--session-id`: The AgentSession model ID to transition. Defaults to `$AGENT_SESSION_ID` env var.
- Exits 0 on success; exits 1 if session not found, session ID not provided, or session is already in a terminal status.
- Calls `transition_status(session, "waiting_for_children")` from `models.session_lifecycle`.

### `agent/sdk_client.py` — PM Dispatch Fan-out Block

The PM dispatch enrichment block in `load_pm_system_prompt()` prepends a `MULTI-ISSUE FAN-OUT` paragraph:

> "If the message contains more than one GitHub issue number, you MUST fan out. For each issue number N, create a child PM session... After spawning ALL children, call wait-for-children..."

### `config/personas/project-manager.md` — Multi-Issue Fan-out Section

The in-repo PM persona overlay includes a `## Multi-Issue Fan-out` section with the same instructions, serving as the authoritative template and documentation for operators.

### `models/session_lifecycle.py` — `_finalize_parent_sync()`

Unchanged by this feature. When each child session reaches a terminal state, `_finalize_parent_sync()` at line 518 checks whether all children are terminal and transitions the parent accordingly. The parent's `waiting_for_children` status is the signal that finalization should propagate.

## Sequential Execution

Children run one at a time via existing project-keyed serialization (introduced in PR #831). No additional scheduling logic is needed — the queue naturally serializes children with the same `project_key` as the parent.

## Race Condition Safety

If a child completes before the parent finishes calling `wait-for-children`, `_finalize_parent_sync()` at lines 571-573 handles this: if the parent isn't in `waiting_for_children` yet when a child finalizes, it sets the parent to `waiting_for_children` itself, then checks all children's statuses. No race here because `enqueue_agent_session()` is synchronous — all children are registered in Redis before `wait-for-children` is called.

## Error Handling

- If `valor_session create` fails mid-fan-out, the parent is not yet in `waiting_for_children`. The worker's existing health-check loop handles the stuck-active parent session.
- If the parent is already in a terminal status when `wait-for-children` is called, the subcommand exits 1 without attempting a transition.
- `_finalize_parent_sync()` has try/except logging at lines 540-548; no new exception paths are introduced.

## Session Management

```bash
# Check the parent and its children
python -m tools.valor_session list --status waiting_for_children
python -m tools.valor_session status --id <parent-id>

# Manually transition a PM session to waiting_for_children
python -m tools.valor_session wait-for-children --session-id <id>

# Kill all children (they're just PM sessions)
python -m tools.valor_session kill --all
```

## Testing

Unit tests in:
- `tests/unit/test_agent_session_hierarchy.py` — `TestCmdWaitForChildren`: transitions status, missing session, no session ID, reads env var, terminal status guard.
- `tests/unit/test_pm_session_factory.py` — `TestPMPersonaFanoutInstruction`: fan-out text present in `sdk_client.py`, `project-manager.md` has the section, references `wait-for-children` and `--role pm`.

## Related Features

- [Session Lifecycle](session-lifecycle.md) — `transition_status()`, `_finalize_parent_sync()`, `waiting_for_children` status
- [PM/Dev Session Architecture](pm-dev-session-architecture.md) — PM session spawning pattern
- [Session Isolation](session-isolation.md) — Project-keyed serialization (sequential child execution)
- [Session Steering](session-steering.md) — `valor-session` CLI, steering inbox
- [Bridge/Worker Architecture](bridge-worker-architecture.md) — Worker picks up child sessions from queue
