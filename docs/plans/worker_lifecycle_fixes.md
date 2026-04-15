---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/984
last_comment_id:
---

# Worker Lifecycle Fixes: Stale Restart Flag + Zombie PID Status

## Problem

Two independent worker-lifecycle bugs cause the worker to self-destruct and then report false health, leaving the queue silently unattended.

**Current behavior:**
1. A restart flag written by the `/update` script fires days later (no TTL), causing the worker to SIGTERM itself after any session completes on a tools-only machine.
2. After the worker shuts down, `worker-status` continues to report `RUNNING` for the zombie PID — the process is in state S (sleeping, 0 CPU) but `pgrep` returns it, so the status check passes. `worker-start` refuses to launch a new process because "the old one is still running."

**Desired outcome:**
- `_check_restart_flag()` ignores flags older than 1 hour. A stale flag from a previous update session never triggers a self-destruct.
- `worker-status` reads the `data/last_worker_connected` heartbeat file and reports `STALE` (not `RUNNING`) when the heartbeat age exceeds the dashboard's threshold (360 seconds), regardless of what `pgrep` says about the PID.

## Freshness Check

**Baseline commit:** `adf37668`
**Issue filed at:** 2026-04-15T06:17:56Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/agent_session_queue.py:2031` — `_check_restart_flag()` — no TTL logic present, still fires on any flag regardless of age
- `agent/agent_session_queue.py:2047` — `_trigger_restart()` — sends `SIGTERM` to `os.getpid()`, kills the worker itself
- `scripts/update/git.py:268` — `set_restart_requested()` — writes `"{timestamp} {commit_count} commit(s)\n"` — timestamp is embedded in file, parseable
- `scripts/valor-service.sh:537` — `get_worker_pid()` — `pgrep -fi "python -m worker"` — no heartbeat check
- `scripts/valor-service.sh:637` — `status_worker()` — PID-only liveness check, no heartbeat file read
- `agent/agent_session_queue.py:1760` — `_write_worker_heartbeat()` — writes `data/last_worker_connected` on every health loop tick

**Cited sibling issues/PRs re-checked:**
- #980 — closed, plan `docs/plans/worker_health_check_on_enqueue.md` (`status: docs_complete`) — covers warning on enqueue, orthogonal to this issue

**Commits on main since issue was filed (touching referenced files):**
- None — `agent/agent_session_queue.py` and `scripts/valor-service.sh` untouched since issue was filed

**Active plans in `docs/plans/` overlapping this area:** `worker_health_check_on_enqueue.md` touches `_write_worker_heartbeat()` and `data/last_worker_connected` for a related purpose (enqueue warning). No conflict — the heartbeat file is read-only from `valor-service.sh`'s perspective.

**Notes:** Dashboard uses `age_s < 360` for "ok", 360–600 for "running", >600 for "error". The `worker-status` fix should use the same 360s threshold for consistency.

## Prior Art

No prior issues found addressing restart flag TTL or zombie PID detection in worker-status. Related health work (#980) focused on enqueue-time warnings rather than the restart flag or the shell status command.

## Research

No relevant external findings — proceeding with codebase context and training data. Both fixes are purely internal: parsing a timestamp embedded in a flat file, and reading a heartbeat file in a shell script.

## Data Flow

### Fix 1: Restart Flag TTL

1. **Entry point**: `scripts/update/run.py:830` calls `set_restart_requested()`, writes `"{timestamp} commit(s)\n"` to `data/restart-requested`
2. **Worker loop**: After each completed session, `_check_restart_flag()` at `agent/agent_session_queue.py:2354` and `:2539` is called
3. **Current**: If file exists and no running sessions → returns `True` → `_trigger_restart()` sends `SIGTERM`
4. **Fixed**: Parse the embedded timestamp from the flag content; if `now - flag_time > 1 hour` → log warning, unlink stale flag, return `False`

### Fix 2: Zombie PID in worker-status

1. **Entry point**: User runs `./scripts/valor-service.sh worker-status`
2. **`status_worker()`**: calls `get_worker_pid()` → `pgrep -fi "python -m worker"` → PID found → prints "RUNNING"
3. **Fixed**: After PID is found, additionally read `data/last_worker_connected` mtime. If heartbeat age > 360s → override status to "STALE (PID exists but heartbeat is stale — worker may be hung)"

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **TTL guard in `_check_restart_flag()`**: Parse the ISO timestamp from the first token of the flag file content. Compare against `datetime.now(UTC)`. If older than 1 hour, log a warning, delete the flag, and return `False`. No new state, no config — just a `datetime.fromisoformat()` comparison.
- **Heartbeat-aware `status_worker()`**: After `get_worker_pid()` returns a non-empty PID, read `data/last_worker_connected` and compute age. If the file is absent or age > 360s, print `Worker Status: STALE` with uptime and heartbeat age. Exit code 2 to distinguish from clean RUNNING (0) and STOPPED (1).
- **Test coverage**: Update `TestRestartFlag` to cover the TTL branch; add a new shell-level or Python-level test for the stale heartbeat case.

### Flow

`update` script runs → writes `data/restart-requested` with timestamp → sessions run for hours → flag checked after session → TTL check: flag is >1h old → flag deleted, no SIGTERM → worker continues

User runs `worker-status` → PID found → heartbeat file read → age > 360s → prints "STALE" → user knows worker is hung → runs `worker-restart`

### Technical Approach

**Fix 1 (`_check_restart_flag`):**
- Read flag file content, split on space, take index 0 as the timestamp string
- `flag_time = datetime.fromisoformat(timestamp_str)` — already UTC-formatted by `set_restart_requested()`
- If `datetime.now(UTC) - flag_time > timedelta(hours=1)`: log `"Restart flag is stale (age=%s) — ignoring and deleting"`, unlink flag, return `False`
- Otherwise proceed as before

**Fix 2 (`status_worker` in `valor-service.sh`):**
- After confirming PID exists, compute heartbeat age:
  ```bash
  HEARTBEAT_FILE="$PROJECT_DIR/data/last_worker_connected"
  if [ -f "$HEARTBEAT_FILE" ]; then
      HEARTBEAT_AGE=$(( $(date +%s) - $(stat -f %m "$HEARTBEAT_FILE" 2>/dev/null || echo 0) ))
  else
      HEARTBEAT_AGE=9999
  fi
  ```
- If `HEARTBEAT_AGE > 360`: print `Worker Status: STALE` + uptime + `Heartbeat: ${HEARTBEAT_AGE}s ago (threshold: 360s)` + return exit code 2
- If `HEARTBEAT_AGE <= 360`: existing RUNNING output unchanged

**No new files. No new config. No new environment variables.**

## Failure Path Test Strategy

### Exception Handling Coverage
- `_check_restart_flag()` currently has no exception handler — the new `datetime.fromisoformat()` call can raise `ValueError` if the flag content is malformed. Add a `try/except ValueError` around the parse: log a warning, unlink the malformed flag, return `False`. Test with a malformed flag file.
- `_write_worker_heartbeat()` already has `except OSError: pass` — no change needed.

### Empty/Invalid Input Handling
- Flag file with no content (empty string) → `split()` returns `[]` → index 0 raises `IndexError` → same `try/except` block as ValueError handles this → log + unlink + return `False`.
- Flag file with only whitespace → `strip()` + `split()` → same path.

### Error State Rendering
- `worker-status` exit code 2 (STALE) is new — callers that check `$?` expecting only 0 or 1 need awareness. No callers currently branch on `worker-status` exit code (verified by grep). Low risk.

## Test Impact

- [ ] `tests/integration/test_remote_update.py::TestRestartFlag::test_check_restart_flag_returns_true_when_flag_exists_and_no_jobs` — UPDATE: test currently writes a flag with a past timestamp from 2026-02-02; this will now return `False` (TTL expired). Rewrite to write a fresh timestamp to confirm the happy path, and add a new test case for the stale path.
- [ ] `tests/integration/test_remote_update.py::TestRestartFlag` — ADD new test: `test_check_restart_flag_ignores_stale_flag` with a timestamp >1h old.
- [ ] `tests/integration/test_remote_update.py::TestRestartFlag` — ADD new test: `test_check_restart_flag_handles_malformed_flag_content`.

## Rabbit Holes

- **Bridge-mode detection** (`HAS_BRIDGE` env var or `machine_type` config): the issue raised this as an option. TTL alone fixes the problem more simply — adding a new machine-type concept to the config system is disproportionate for a 1-hour TTL guard.
- **Replacing `pgrep` with a socket ping**: overkill. The heartbeat file is the existing liveness signal; reading it from the shell is trivial.
- **Centralizing `worker-status` in Python**: the shell script is authoritative for service management; mixing Python here complicates the startup/restart flow.

## Risks

### Risk 1: TTL too short for slow deployments
**Impact:** If a `/update` run takes >1h (very slow machine, large pull), the restart flag expires before the worker processes it.
**Mitigation:** 1 hour is generous — typical update runs finish in <2 minutes. Flag is only needed for the first idle moment after the update; if the worker processes a session in that window it was never truly idle anyway.

### Risk 2: `stat -f %m` is macOS-specific
**Impact:** On Linux machines, `stat -f %m` fails; Linux uses `stat -c %Y`.
**Mitigation:** Use `python3 -c "import os,time; print(int(time.time()-os.path.getmtime('$HEARTBEAT_FILE')))"` for portability. This is a one-liner and avoids the `stat` flag discrepancy.

## Race Conditions

### Race 1: Flag written between TTL check and unlink
**Location:** `agent/agent_session_queue.py:_check_restart_flag`
**Trigger:** The update script writes a fresh flag after `_check_restart_flag` reads the old (stale) flag timestamp but before it calls `unlink(missing_ok=True)`.
**Data prerequisite:** The new flag must have a fresh timestamp.
**State prerequisite:** Worker must not be in a SIGTERM window.
**Mitigation:** After the TTL check decides `False`, call `_RESTART_FLAG.unlink(missing_ok=True)`. If a fresh flag was written between the read and the unlink, it is deleted. This is acceptable — the update script's next poll interval will re-write it (cron runs periodically). The risk of losing a legitimate restart signal is minimal vs. the cost of the stale-flag self-destruct.

No race conditions identified for the `worker-status` shell change — it is a read-only check.

## No-Gos (Out of Scope)

- Bridge-mode awareness / machine-type config — TTL is simpler and sufficient
- Changing how the update script writes the flag (no cron/TTL on the write side)
- Replacing `pgrep` with a health endpoint or TCP ping
- Fixing `worker-start` to force-kill zombie PIDs automatically (separate issue if desired)

## Update System

No update system changes required — both fixes are purely internal to `agent/agent_session_queue.py` and `scripts/valor-service.sh`. The update script's `set_restart_requested()` format (embedded timestamp) is unchanged; we are only adding a consumer-side TTL check.

## Agent Integration

No agent integration required — this is a worker-internal and service-script change. No new MCP tools, no bridge changes.

## Documentation

- [ ] Update `docs/features/bridge-self-healing.md` — add a note that the restart flag has a 1-hour TTL and that `worker-status` reports STALE when the heartbeat age exceeds 360s. The existing doc covers the self-healing restart mechanism; this adds the TTL and zombie-detection behavior.

## Success Criteria

- [ ] `_check_restart_flag()` returns `False` for a flag with a timestamp >1 hour old, and deletes the stale flag
- [ ] `_check_restart_flag()` still returns `True` for a fresh flag (<1 hour old) with no running sessions
- [ ] Malformed or empty flag content does not raise an exception — logs warning and returns `False`
- [ ] `./scripts/valor-service.sh worker-status` prints `Worker Status: STALE` when PID exists but heartbeat file is absent or >360s old
- [ ] `./scripts/valor-service.sh worker-status` prints `Worker Status: RUNNING` as before when PID exists and heartbeat is fresh
- [ ] Existing `TestRestartFlag` tests updated and passing
- [ ] New TTL and malformed-flag tests passing
- [ ] Tests pass (`/do-test`)
- [ ] `docs/features/bridge-self-healing.md` updated

## Team Orchestration

### Team Members

- **Builder (worker-fixes)**
  - Name: worker-fixes-builder
  - Role: Implement TTL guard in `_check_restart_flag()` and heartbeat-aware `status_worker()`
  - Agent Type: builder
  - Resume: true

- **Validator (worker-fixes)**
  - Name: worker-fixes-validator
  - Role: Verify TTL logic, test coverage, shell script changes, and docs update
  - Agent Type: validator
  - Resume: true

### Available Agent Types

builder, validator, documentarian

## Step by Step Tasks

### 1. Implement restart flag TTL guard
- **Task ID**: build-restart-flag-ttl
- **Depends On**: none
- **Validates**: `tests/integration/test_remote_update.py::TestRestartFlag`
- **Assigned To**: worker-fixes-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/agent_session_queue.py`, modify `_check_restart_flag()`: parse the embedded ISO timestamp from the flag content; if age > 1h, log warning, unlink flag, return `False`; wrap parse in `try/except (ValueError, IndexError)` → log + unlink + return `False`
- Update `tests/integration/test_remote_update.py::TestRestartFlag::test_check_restart_flag_returns_true_when_flag_exists_and_no_jobs` to write a fresh timestamp
- Add `test_check_restart_flag_ignores_stale_flag` (timestamp >1h old → returns `False`, file deleted)
- Add `test_check_restart_flag_handles_malformed_flag_content` (empty/bad content → returns `False`, no exception)

### 2. Implement heartbeat-aware worker-status
- **Task ID**: build-worker-status-heartbeat
- **Depends On**: none
- **Assigned To**: worker-fixes-builder
- **Agent Type**: builder
- **Parallel**: true
- In `scripts/valor-service.sh`, modify `status_worker()`: after PID is confirmed, read `data/last_worker_connected` mtime using `python3 -c "..."` one-liner for cross-platform portability; if age > 360s print `Worker Status: STALE` with uptime and heartbeat age, return exit code 2; otherwise existing RUNNING output unchanged

### 3. Update bridge-self-healing docs
- **Task ID**: document-fixes
- **Depends On**: build-restart-flag-ttl, build-worker-status-heartbeat
- **Assigned To**: worker-fixes-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/bridge-self-healing.md` — add note about 1-hour TTL on restart flag and STALE status in worker-status

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-restart-flag-ttl, build-worker-status-heartbeat, document-fixes
- **Assigned To**: worker-fixes-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/integration/test_remote_update.py::TestRestartFlag -v`
- Run `python -m ruff check agent/agent_session_queue.py`
- Verify `docs/features/bridge-self-healing.md` mentions TTL and STALE
- Confirm no existing tests regressed

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/integration/test_remote_update.py::TestRestartFlag -v` | exit code 0 |
| Lint clean | `python -m ruff check agent/agent_session_queue.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/agent_session_queue.py` | exit code 0 |
| Full test suite | `pytest tests/ -x -q` | exit code 0 |
| TTL test exists | `grep -n "stale_flag\|stale flag" tests/integration/test_remote_update.py` | output > 0 |
| Heartbeat check in script | `grep -n "last_worker_connected" scripts/valor-service.sh` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

None — the fix scope is clear. Both bugs have deterministic root causes with straightforward mitigations.
