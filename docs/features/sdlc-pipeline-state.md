# SDLC Pipeline State Tracking

Stage progress for the SDLC pipeline is tracked in Redis via `PipelineStateMachine` on the PM session's `stage_states` field.

## How It Works

Each SDLC sub-skill (do-plan, do-build, do-docs, etc.) writes stage markers at start and completion:

```bash
python -m tools.sdlc_stage_marker --stage PLAN --status in_progress --issue-number 941
python -m tools.sdlc_stage_marker --stage PLAN --status completed --issue-number 941
```

The SDLC router queries current state before dispatching:

```bash
python -m tools.sdlc_stage_query --issue-number 941
```

## Session Resolution

The stage marker resolves the PM session in this order:

1. `--session-id` argument (explicit)
2. `VALOR_SESSION_ID` env var (bridge-injected)
3. `AGENT_SESSION_ID` env var (alternative)
4. `--issue-number` argument (local Claude Code sessions)

For local sessions, `--issue-number` is the primary path since env vars don't persist across Claude Code bash blocks.

## Local Session Creation

Before dispatching sub-skills, the SDLC router ensures a local session exists:

```bash
python -m tools.sdlc_session_ensure --issue-number 941 --issue-url "https://github.com/owner/repo/issues/941"
```

This creates an `AgentSession` with `session_id="sdlc-local-941"` and `session_type="pm"`. It's idempotent — running it again returns the existing session.

## Key Files

| File | Purpose |
|------|---------|
| `tools/sdlc_stage_marker.py` | Write stage markers (in_progress/completed) |
| `tools/sdlc_stage_query.py` | Query current stage states |
| `tools/sdlc_session_ensure.py` | Create/find local SDLC sessions |
| `tools/_sdlc_utils.py` | Shared `find_session_by_issue()` helper |
| `agent/pipeline_state.py` | `PipelineStateMachine` reads/writes `stage_states` |

## Bridge vs Local

- **Bridge sessions**: Worker injects `VALOR_SESSION_ID` env var. Markers resolve via env var. Hooks also fire.
- **Local sessions**: No env var available. `--issue-number` resolves via `find_session_by_issue()` scanning PM sessions by `issue_url` suffix.

Both paths write to the same `stage_states` field on the PM session, so the merge gate and stage query work identically.
