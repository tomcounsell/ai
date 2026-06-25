# Eng Session Child Fan-out

## Overview

When an eng session receives a message containing multiple GitHub issue numbers (e.g., "Run SDLC on issues 777, 775, 776"), it fans out: instead of handling all issues in a single session with growing context, it spawns one child eng session per issue and pauses itself until all children complete.

## Trigger Conditions

Fan-out activates when:
- The eng session's message text contains **more than one GitHub issue number** requiring SDLC work.
- Examples: "Run SDLC on issues 777, 775, 776", "Process #777 and #775", "777, 775, 776".

Fan-out does **not** activate for status queries (e.g., "what's the status of 777 and 775?") — the eng session answers those directly.

## Data Flow

1. **Parent eng session** receives the multi-issue message.
2. **Fan-out**: the eng session runs one `valor_session create --role eng` call per issue:
   ```bash
   python -m tools.valor_session create \
     --role eng \
     --parent "$AGENT_SESSION_ID" \
     --message "Run SDLC on issue 777"
   ```
3. **Pause**: After spawning all children, the eng session calls:
   ```bash
   python -m tools.valor_session wait-for-children --session-id "$AGENT_SESSION_ID"
   ```
   This transitions the parent to `waiting_for_children`.
4. **Stay silent through fan-out**: The eng persona instructs the session to *not* pre-announce fan-out — no "Spawning 3 child sessions...", no session IDs. The supervisor sees each child's output as it arrives; the parent speaks again only when something needs input or all children are done (see `config/personas/engineer.md` Multi-Issue Fan-Out).
5. **Child execution**: The worker picks up each child eng session via project-keyed serialization (one at a time). Each child runs its own isolated SDLC pipeline.
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

### `agent/sdk_client.py` — Eng Dispatch Fan-out Block

In the SDLC dispatch branch (the `else` path that handles non-teammate, non-collaboration work), the dispatch flow appends a `MULTI-ISSUE FAN-OUT` paragraph to the enriched message:

> "If the message contains more than one GitHub issue number, you MUST fan out. For each issue number N, run `valor_session create --role eng`... After spawning ALL children, run wait-for-children to pause this session. Stay silent through fan-out — no narration, no session IDs."

(The eng persona overlay loaded by `load_eng_system_prompt()` carries the same instruction; see the next section.)

### `config/personas/engineer.md` — Multi-Issue Fan-Out Section

The in-repo engineer persona overlay includes a `## Multi-Issue Fan-Out` section with the same instructions, serving as the authoritative template and documentation for operators.

### `models/session_lifecycle.py` — `_finalize_parent_sync()`

Unchanged by this feature. When each child session reaches a terminal state, `_finalize_parent_sync()` at line 518 checks whether all children are terminal and transitions the parent accordingly. The parent's `waiting_for_children` status is the signal that finalization should propagate.

## Execution Model

Children execute via `worker_key`-based routing (issue #1228 extended this for eng sessions). At PLAN/CRITIQUE stages, children with the same `project_key` serialize naturally via the project-keyed worker loop (introduced in PR #831). At BUILD/TEST/REVIEW/DOCS stages, children with distinct slugs each get their own slug-keyed worker loop and can execute in parallel, reducing total wall time from `sum(child_runtimes)` to `max(child_runtimes)` for worktree-stage work. No additional scheduling logic is needed — `AgentSession.worker_key` determines the routing automatically based on the child's current stage.

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

# Manually transition an eng session to waiting_for_children
python -m tools.valor_session wait-for-children --session-id <id>

# Kill all children (they're just eng sessions)
python -m tools.valor_session kill --all
```

## Testing

Unit tests in:
- `tests/unit/test_agent_session_hierarchy.py` — `TestCmdWaitForChildren`: transitions status, missing session, no session ID, reads env var, terminal status guard.
- `tests/unit/test_pm_session_factory.py` — `TestEngPersonaFanoutInstruction`: fan-out text present in `sdk_client.py`, `engineer.md` has the section, references `wait-for-children` and `--role eng`.

## Related Features

- [Session Lifecycle](session-lifecycle.md) — `transition_status()`, `_finalize_parent_sync()`, `waiting_for_children` status
- [Eng Session Architecture](eng-session-architecture.md) — eng session spawning pattern
- [Session Isolation](session-isolation.md) — Project-keyed serialization (sequential child execution)
- [Session Steering](session-steering.md) — `valor-session` CLI, steering inbox
- [Bridge/Worker Architecture](bridge-worker-architecture.md) — Worker picks up child sessions from queue
