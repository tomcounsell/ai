---
status: Ready
type: bug
appetite: Small
owner: Tom Counsell
created: 2026-04-07
tracking: https://github.com/tomcounsell/ai/issues/804
last_comment_id:
---

# fix: valor-session kill uses wrong lifecycle function for terminal status

## Problem

`python -m tools.valor_session kill --id <ID>` and `kill --all` always throw a `ValueError` and fail to kill any sessions.

**Current behavior:**
`cmd_kill()` in `tools/valor_session.py` calls `transition_status(s, "killed", ...)` at lines 408 and 431. `transition_status()` raises `ValueError` for any terminal status (completed, failed, killed, abandoned, cancelled), leaving sessions stuck in `running` or `pending` state.

**Desired outcome:**
Both `kill --id` and `kill --all` successfully transition sessions to `killed` status without errors.

## Prior Art

- **Issue #701**: Consolidate AgentSession lifecycle mutations into single-entrypoint functions — closed 2026-04-05. This issue established the `finalize_session()` / `transition_status()` split. The enforcement guard in `transition_status()` was added as part of this consolidation. The `valor_session.py` CLI was not updated at that time, leaving it calling the wrong function.
- **Issue #783**: AgentSession status index corruption: ghost running sessions — closed 2026-04-07. Related lifecycle cleanup work but did not touch `valor_session.py` `cmd_kill()`.

## Data Flow

1. **Entry**: User runs `python -m tools.valor_session kill --id <ID>` or `kill --all`
2. **`cmd_kill()`**: Queries Redis for matching `AgentSession` records via `AgentSession.query.filter()`
3. **Lifecycle call**: Currently calls `transition_status(session, "killed", ...)` — raises `ValueError`
4. **Fix**: Call `finalize_session(session, "killed", ...)` instead — sets status + `completed_at` + saves

## Solution

Two-line fix in `tools/valor_session.py`:

1. **Line 396** — add `finalize_session` to the import:
   ```python
   from models.session_lifecycle import TERMINAL_STATUSES, finalize_session, transition_status
   ```

2. **Line 408** — replace the `--all` loop call:
   ```python
   finalize_session(s, "killed", reason="valor-session kill --all")
   ```

3. **Line 431** — replace the `--id` call:
   ```python
   finalize_session(session, "killed", reason="valor-session kill")
   ```

`transition_status` can remain imported if used elsewhere in the file; if not, remove it.

## Appetite

Small — surgical 3-line fix plus one new test file.

## Step by Step Tasks

- [ ] Read `tools/valor_session.py` lines 390–450 to confirm exact line numbers and surrounding context
- [ ] Add `finalize_session` to the import on line 396
- [ ] Replace `transition_status(s, "killed", ...)` at line 408 with `finalize_session(s, "killed", ...)`
- [ ] Replace `transition_status(session, "killed", ...)` at line 431 with `finalize_session(session, "killed", ...)`
- [ ] Remove `transition_status` from the import if it is no longer used elsewhere in `cmd_kill()`
- [ ] Create `tests/unit/test_valor_session_kill.py` covering `cmd_kill()` with `--id` and `--all` flags (unit, no Redis)
- [ ] Run `pytest tests/unit/test_valor_session_kill.py -v` — must pass

## Success Criteria

- `python -m tools.valor_session kill --id <ID>` transitions the session to `killed` without ValueError
- `python -m tools.valor_session kill --all` kills all non-terminal sessions without error
- Unit tests in `tests/unit/test_valor_session_kill.py` pass for both `--id` and `--all` paths
- No existing tests broken

## No-Gos

- Do not refactor `cmd_kill()` beyond swapping the function call
- Do not change `finalize_session()` or `transition_status()` signatures
- Do not touch `agent_session_scheduler.py` (it already correctly uses `finalize_session`)

## Failure Path Test Strategy

- Mock `AgentSession.query.filter()` to return fake sessions
- Mock `finalize_session` to verify it is called with `"killed"` and correct `reason`
- Verify `cmd_kill` returns `0` on success, `1` on error
- Test that already-terminal sessions are skipped without error

## Test Impact

No existing tests affected — `cmd_kill()` in `tools/valor_session.py` has no existing test coverage. The parallel `cmd_kill()` in `tools/agent_session_scheduler.py` has full coverage in `tests/unit/test_agent_session_scheduler_kill.py` and is not touched by this fix.

## Rabbit Holes

- Do NOT investigate why `agent_session_scheduler.py` was correct while `valor_session.py` was not — the fix is the fix
- Do NOT add `skip_auto_tag` / `skip_checkpoint` kwargs to the `finalize_session` calls unless there is a concrete reason (the default behavior is appropriate for a CLI kill)

## Update System

No update system changes required — this is a purely internal bug fix to a CLI tool.

## Agent Integration

No agent integration changes required — `valor_session` is a CLI tool, not an MCP-exposed tool.

## Documentation

No documentation changes needed — this is a single-function bug fix that corrects a wrong import/call. The correct contract (`finalize_session` for terminal statuses, `transition_status` for non-terminal statuses) is already documented in the module docstring at the top of `models/session_lifecycle.py`. No user-facing behavior or API surface changes.

## Open Questions

None — root cause is fully understood and the fix is unambiguous.
