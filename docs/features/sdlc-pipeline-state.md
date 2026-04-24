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

### Bridge short-circuit

Inside a bridge-initiated session (where `VALOR_SESSION_ID` is exported by `agent/sdk_client.py`), `ensure_session` short-circuits immediately:

1. Read `VALOR_SESSION_ID` (or `AGENT_SESSION_ID`) from the environment.
2. Resolve the session via `tools._sdlc_utils.find_session(session_id=...)`.
3. Confirm `session_type == "pm"` and `status not in TERMINAL_STATUSES`.
4. Return the bridge session id with `created: false` — no `sdlc-local-{N}` record is created.

The short-circuit falls through to the legacy create path when:

- The env var is unset or empty.
- The env-resolved session does not exist in Redis (stale env).
- The env-resolved session has `session_type != "pm"` (e.g., a Dev session during cross-role debugging).
- The env-resolved session has a terminal status (completed, killed, abandoned, failed, cancelled).

The message-text fallback inside `find_session_by_issue` is a secondary defense for degraded scenarios where `VALOR_SESSION_ID` is missing but a bridge session exists with `issue_url=None` and `message_text="SDLC issue {N}"`. It matches the issue number inside `message_text` using a word-boundary regex (`\bissue\s*#?\s*{N}\b`, case-insensitive) so `tissue 1147` does not false-match.

### Orphan cleanup

Legacy zombie `sdlc-local-{N}` sessions (running status, no heartbeats, older than 10 minutes) can be listed and finalized with:

```bash
# Preview without modifying (exits 0, prints JSON list)
python -m tools.sdlc_session_ensure --kill-orphans --dry-run

# Finalize each via models.session_lifecycle.finalize_session
python -m tools.sdlc_session_ensure --kill-orphans
```

The CLI always exits 0. Per-session finalize failures are reported inside the JSON payload's `failures` count and per-session `result` list — they never raise. When non-zero zombies are detected, a single stderr line (`[sdlc_session_ensure] found N zombie sdlc-local session(s)`) surfaces the count to scheduled-cleanup operators while stdout stays machine-parseable.

## Key Files

| File | Purpose |
|------|---------|
| `tools/sdlc_stage_marker.py` | Write stage markers (in_progress/completed) |
| `tools/sdlc_stage_query.py` | Query current stage states |
| `tools/sdlc_session_ensure.py` | Create/find local SDLC sessions |
| `tools/_sdlc_utils.py` | Shared `find_session_by_issue()` helper |
| `agent/pipeline_state.py` | `PipelineStateMachine` reads/writes `stage_states` |

## Bridge vs Local

- **Bridge sessions**: Worker injects `VALOR_SESSION_ID` env var. Markers resolve via env var. `sdlc_session_ensure` short-circuits and does not create an `sdlc-local-{N}` record. Hooks also fire.
- **Local sessions**: No env var available. `--issue-number` resolves via `find_session_by_issue()` scanning PM sessions by `issue_url` suffix and (fallback) by `message_text` regex.

Both paths write to the same `stage_states` field on the PM session, so the merge gate and stage query work identically.
