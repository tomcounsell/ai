# Plan: Fix Killed Sessions Resurrecting in Running Index

**Issue:** #1006
**Slug:** `fix-zombie-session-index`
**Branch:** `session/fix-zombie-session-index`

## Problem

When sessions are killed via `valor-session kill` or `agent-session-scheduler kill`, the Popoto `IndexedField` status index isn't reliably cleaned. On worker restart or health check cycle, killed sessions reappear in the `running` index and get re-promoted to `pending`, creating an infinite resurrection cycle.

## Root Cause

Two dimensions:

1. **Index corruption persists**: `rebuild_indexes()` at worker startup re-indexes all sessions from their Redis hash data. If the hash is correct (status=killed), rebuild is fine. But the startup recovery function (`_recover_interrupted_agent_sessions_startup`) runs *after* rebuild and trusts the `running` index without verifying the hash status — so if any stale index entry survived, it gets promoted to pending.

2. **No terminal-status guard in consumers**: Both `_recover_interrupted_agent_sessions_startup()` and `_agent_session_health_check()` query `AgentSession.query.filter(status="running")` and act on results without re-reading the actual hash status to confirm the session is truly running. A stale index entry for a killed session gets recovered as if it were a legitimate orphan.

## Solution

### Dimension 1: Terminal-status guards in health check and startup recovery

Add a guard at the top of both loops that re-reads the session's actual status from the hash and skips (with cleanup) any session whose hash status is terminal.

**Files changed:**
- `agent/agent_session_queue.py` — `_recover_interrupted_agent_sessions_startup()` (~line 1345)
- `agent/agent_session_queue.py` — `_agent_session_health_check()` (~line 1553)

### Dimension 2: Use `repair_indexes` instead of `rebuild_indexes` for stale-entry cleanup

When a stale index entry is detected in `_pop_job()`, use `repair_indexes()` (which clears IndexedField indexes before rebuilding) instead of `rebuild_indexes()` (which only clears KeyField/SortedField indexes). This ensures orphan entries in `$IndexF:` sets are actually removed.

**Files changed:**
- `agent/agent_session_queue.py` — `_pop_job()` stale-entry handler (~line 813, ~line 951)

## No-Gos

- Do not change the `finalize_session()` or `transition_status()` functions — they already have defensive srem
- Do not break legitimate recovery of truly orphaned running sessions from crashed workers
- Do not add expensive operations to the health check hot path — terminal check is a single hash read

## Update System

No update system changes required — this is a worker-internal bugfix with no new dependencies or config.

## Agent Integration

No agent integration required — this is internal to the worker health check and startup recovery paths.

## Test Impact

- [ ] `tests/unit/test_session_lifecycle.py` — no changes expected (lifecycle functions unchanged)
- [ ] `tests/unit/test_health_check_recovery_finalization.py` — UPDATE: may need new test cases for terminal-status guard

## Failure Path Test Strategy

- Test: Kill a session, simulate health check finding it in running index, assert it stays killed
- Test: Kill a session, simulate startup recovery finding it in running index, assert it stays killed
- Test: Legitimate orphaned running session (non-terminal hash status) still gets recovered

## Rabbit Holes

- Do not attempt to fix Popoto's internal IndexedField srem logic — work around it with defense-in-depth
- Do not add Redis WATCH/MULTI/EXEC transactions — the Python-level CAS is sufficient

## Documentation

- [ ] Update `docs/features/agent-session-queue.md` with note about terminal-status guards
- No new documentation files needed — this is a bugfix

## Tasks

- [ ] Add terminal-status guard to `_recover_interrupted_agent_sessions_startup()`
- [ ] Add terminal-status guard to `_agent_session_health_check()`
- [ ] Replace `rebuild_indexes()` with `repair_indexes()` in `_pop_job()` stale-entry handlers
- [ ] Write regression tests for zombie session resurrection
- [ ] Verify existing tests pass
