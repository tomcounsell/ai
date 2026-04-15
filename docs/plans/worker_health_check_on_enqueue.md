---
status: Planning
type: bug
appetite: Small
owner: Valor
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/980
last_comment_id:
revision_applied: true
allow_unchecked: true

---

# Worker Health Check on Enqueue

## Problem

On machines where the worker service is stopped (skills/tools-only machines), `python -m tools.valor_session create` returns success and prints a session ID. The session sits in `pending` forever. No warning is printed at creation time and no indication surfaces when checking `status --id <ID>`.

**Current behavior:**
1. `valor_session create` enqueues a session and returns `Created session: <ID>` — looks normal.
2. `valor_session status --id <ID>` shows `status: pending` indefinitely with no indication the queue is unattended.
3. `agent_session_scheduler status` returns counts but no `worker_healthy` field.

**Desired outcome:**
- At session creation time, a warning is printed to stderr if the worker heartbeat is stale or absent.
- On `valor_session status --id <ID>`, a "queue has no active worker" warning is shown when the session is pending and the worker appears dead.
- `agent_session_scheduler status` includes a `worker_healthy` boolean field so automated shepherding can detect the no-worker condition programmatically.

## Freshness Check

**Baseline commit:** 2aa04c408adad6ceea80070ff7408fce13329917
**Issue filed at:** 2026-04-15T03:58:44Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `tools/valor_session.py:135` — `cmd_create()` — still holds: no worker health check present
- `agent/agent_session_queue.py:1760` — `_write_worker_heartbeat()` — still present, writes `data/last_worker_connected`
- `ui/app.py:198` — `_get_worker_health()` — still present, reads heartbeat file with 360s threshold
- `tools/agent_session_scheduler.py:434` — `cmd_status()` — still holds: no `worker_healthy` field in output

**Cited sibling issues/PRs re-checked:**
- #971 — still open (unresolved)
- #260 — still open (unresolved)

**Commits on main since issue was filed (touching referenced files):**
- None — no commits to `tools/valor_session.py`, `tools/agent_session_scheduler.py`, `agent/agent_session_queue.py` since issue was filed.

**Active plans in `docs/plans/` overlapping this area:** none

**Notes:** The dashboard's `_get_worker_health()` uses age < 360s for "ok", 360–600s for "running", >600s for "error". The CLI tools should use the same thresholds for consistency.

## Prior Art

No prior issues found specifically addressing worker health surfacing at enqueue time. Related health work focused on session health monitoring rather than pre-enqueue warnings.

## Data Flow

1. **Entry point**: `valor_session create` CLI call
2. **`cmd_create` in `tools/valor_session.py`**: reads args, calls `_push_agent_session()`, returns session ID
3. **Missing check**: no read of `data/last_worker_connected` before or after enqueue
4. **Queue**: session lands in Redis as `pending`
5. **Worker absent**: no process polling the queue — session stays `pending` indefinitely

For the status path:
1. **`valor_session status --id <ID>`**: reads `AgentSession` from Redis
2. **Missing check**: no heartbeat check when status is `pending`
3. **User sees**: `status: pending` with no additional context

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. The heartbeat file `data/last_worker_connected` is already written by the running worker.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis accessible | `python -c "import redis; redis.Redis().ping()"` | AgentSession reads require Redis |

## Solution

### Key Elements

- **Worker health reader**: A small helper function `_check_worker_health()` that reads `data/last_worker_connected` and returns `(healthy: bool, age_s: int | None)`. Mirrors the logic already in `ui/app.py:_get_worker_health()`.
- **Enqueue warning**: In `cmd_create`, after `_push_agent_session()` succeeds, call `_check_worker_health()` and print a stderr warning if the worker appears down.
- **Status warning**: In `cmd_status`, when the session status is `pending`, call `_check_worker_health()` and append a "WARNING: no active worker" line to the output.
- **Scheduler status field**: In `agent_session_scheduler.cmd_status`, add a `worker_healthy` boolean to the result dict, along with `worker_heartbeat_age_s`.

### Flow

`valor_session create` → session enqueued → **worker health check** → if stale: print warning to stderr → return session ID

`valor_session status --id X` → fetch session → if `status == "pending"` → **worker health check** → if stale: print warning → show status

`agent_session_scheduler status` → collect counts → **read heartbeat file** → add `worker_healthy` field → print result

### Technical Approach

- **Single helper in valor_session.py**: Add `_check_worker_health() -> tuple[bool, int | None]` to `tools/valor_session.py` (self-contained). The function reads the heartbeat file's modification time via `heartbeat_file.stat().st_mtime`, computes age as `int(time.time() - mtime)`, and returns `(True, age_s)` if age < 360 else `(False, age_s)`. Returns `(False, None)` if file missing or any exception. This matches the established pattern in `ui/app.py:_get_worker_health()` — no ISO string parsing, no format dependency.
- **Duplicate inline in agent_session_scheduler.py**: Do NOT import `_check_worker_health` from `tools/valor_session`. Instead, add an equivalent 5-line inline check directly in `agent_session_scheduler.py:cmd_status`. Using `stat().st_mtime` means there is no string parsing, so malformed content handling is no longer needed. Avoids coupling two independent tool files.
- **Warning only on `create`** — do not fail. The `--force` flag mentioned in the issue as a future escape hatch is a rabbit hole for v1; the warning is enough.
- **Heartbeat file path** is relative to repo root (`data/last_worker_connected`). Resolve via `Path(__file__).parent.parent / "data" / "last_worker_connected"` same as `agent_session_queue.py`.
- **Missing file == unhealthy**: if the file doesn't exist, the worker has never run on this machine — treat as unhealthy.
- **JSON mode guards for cmd_status**: In `cmd_status`, the plain-text warning is guarded by `if not args.json:` before printing to stderr. The `worker_healthy` field is always added to the JSON dict regardless of health state. This prevents automated pollers from having stderr flooded on every status check.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The `_check_worker_health()` helper must catch `OSError` silently — missing file or permission error must never crash `create` or `status`
- [ ] If Redis is unavailable, the status command already handles it; the added health check is file-based and independent

### Empty/Invalid Input Handling
- [ ] `_check_worker_health()` uses `stat().st_mtime` — no string parsing, so malformed file content cannot raise; only `OSError` (missing/permission) and unexpected exceptions need catching
- [ ] `age_s` returns `None` when file is missing; callers must handle `None` (treat as unhealthy)

### Error State Rendering
- [ ] Warning must print to stderr (not stdout) so JSON output mode (`--json`) is not polluted
- [ ] In `cmd_create`: JSON mode adds `worker_healthy` to the output dict instead of printing a plain-text warning to stderr
- [ ] In `cmd_status`: plain-text warning is guarded by `if not args.json:` — automated pollers using `--json` receive no stderr noise; `worker_healthy` field is always present in the JSON dict
- [ ] `agent_session_scheduler status` always emits `worker_healthy` in the JSON result regardless of health state

## Test Impact

- [ ] `tests/unit/test_valor_session.py` — UPDATE: add test cases for `_check_worker_health()` helper (mock file read), and verify warning appears in stderr output when worker is stale
- [ ] `tests/unit/test_agent_session_scheduler.py` (if it exists) — UPDATE: assert `worker_healthy` field is present in `cmd_status` output

No existing behavioral tests will break — this is purely additive; no existing code paths are modified, only extended.

## Rabbit Holes

- **`--force` flag on `create`**: Tempting as an escape hatch. Out of scope for this fix — warnings are sufficient and `create` should never silently block.
- **Refuse to create if no worker**: Making `create` fail when worker is absent could break automated shepherding scripts that create sessions before starting a worker. Warn only.
- **Worker start on demand**: Automatically starting the worker when no heartbeat is found. Too much coupling — not this plan's job.
- **Per-project worker health**: The heartbeat file is global (one worker per machine). Multi-project per-machine health differentiation is a separate concern.

## Risks

### Risk 1: Heartbeat file path assumption
**Impact:** If the repo root changes or `data/` directory is missing, the health check raises OSError.
**Mitigation:** Wrap in try/except; missing file == unhealthy. No crash.

### Risk 2: Clock skew on remote machines
**Impact:** If system clock is wrong, the age calculation is wrong and a healthy worker appears stale.
**Mitigation:** The 360s threshold is generous enough that minor clock drift is safe to ignore.

## Race Conditions

No race conditions identified — the health check is a read-only file stat operation. The check result is informational only; no state changes depend on it.

## No-Gos (Out of Scope)

- `--force` flag on `valor_session create` to bypass the warning
- Auto-starting the worker when health check fails
- Per-project worker health (current worker is single-instance per machine)
- Making `create` return a non-zero exit code when no worker is present

## Update System

No update system changes required — this feature is purely internal. The `data/last_worker_connected` file is already created by the worker on first run.

## Agent Integration

No agent integration required — `tools/valor_session.py` and `tools/agent_session_scheduler.py` are already exposed as MCP tools. The new `worker_healthy` field in `scheduler status` JSON output is automatically available to any caller reading that output. No `.mcp.json` changes needed.

## Documentation

- [ ] Update `docs/features/session-management.md` (or create if absent) to document the worker health check behavior at enqueue time
- [ ] Add a note to the CLAUDE.md Quick Commands table that `valor_session create` now warns when no worker is running

## Success Criteria

- [ ] `python -m tools.valor_session create --role pm --message "test"` prints a stderr warning when `data/last_worker_connected` is absent or older than 360s
- [ ] `python -m tools.valor_session create --json --role pm --message "test"` includes `"worker_healthy": false` in JSON output when worker is absent
- [ ] `python -m tools.valor_session status --id <ID>` prints a "WARNING: no active worker" line when the session status is `pending` and worker heartbeat is stale
- [ ] `python -m tools.agent_session_scheduler status` JSON output includes `"worker_healthy": true/false` and `"worker_heartbeat_age_s": N|null`
- [ ] `_check_worker_health()` never raises an exception (all OSError paths are caught)
- [ ] Tests pass (`/do-test`)
- [ ] Lint clean (`python -m ruff check .`)

## Team Orchestration

### Team Members

- **Builder (cli-health)**
  - Name: cli-health-builder
  - Role: Add `_check_worker_health()` helper and wire it into `cmd_create`, `cmd_status`, and `agent_session_scheduler.cmd_status`
  - Agent Type: builder
  - Resume: true

- **Validator (cli-health)**
  - Name: cli-health-validator
  - Role: Verify all three touch points behave correctly and no existing tests are broken
  - Agent Type: validator
  - Resume: true

### Available Agent Types

builder, validator

## Step by Step Tasks

### 1. Add `_check_worker_health()` helper and wire into all three touch points

- **Task ID**: build-worker-health-check
- **Depends On**: none
- **Validates**: `tests/unit/test_valor_session.py`, `tests/unit/test_agent_session_scheduler.py`
- **Assigned To**: cli-health-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_check_worker_health() -> tuple[bool, int | None]` to `tools/valor_session.py`. Use `heartbeat_file.stat().st_mtime` to read modification time, compute `age_s = int(time.time() - mtime)`. Return `(True, age_s)` if age_s < 360 else `(False, age_s)`. Return `(False, None)` if file missing or any exception. Wrap all in try/except. Do NOT import this helper into `agent_session_scheduler.py` — add an equivalent 5-line inline there instead to keep tool files decoupled.
- In `cmd_create` (after `asyncio.run(_create())`): call `_check_worker_health()`. If not healthy and not JSON mode, print `"WARNING: no active worker detected — session will stay pending until a worker is started (run: ./scripts/valor-service.sh worker-start)"` to stderr. In JSON mode, add `"worker_healthy": False` to the output dict instead of printing.
- In `cmd_status`: after determining `session.status`, if status is `"pending"`, call `_check_worker_health()`. Guard the plain-text warning with `if not args.json:` — print `"  WARNING: No active worker — session may wait indefinitely."` to stderr only in non-JSON mode. Always add `"worker_healthy"` field to the JSON dict regardless.
- In `tools/agent_session_scheduler.py` `cmd_status` function: add an inline 5-line version of the health check using `stat().st_mtime` (do not import from `valor_session`). Add `"worker_healthy": bool` and `"worker_heartbeat_age_s": int | None` fields to the `result` dict before `_output(result)`.
- Write unit tests in `tests/unit/test_valor_session.py`: mock the heartbeat file using `tmp_path`; test (a) healthy case, (b) stale case (mtime older than 360s), (c) missing file. Assert warning appears in stderr for stale/missing, not for healthy. Assert JSON output includes `worker_healthy` field. Note: no malformed-content test needed since `stat().st_mtime` doesn't parse file content.

### 2. Validate

- **Task ID**: validate-worker-health-check
- **Depends On**: build-worker-health-check
- **Assigned To**: cli-health-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_valor_session.py -v` — must pass
- Run `python -m ruff check tools/valor_session.py tools/agent_session_scheduler.py` — must be clean
- Run `python -m tools.agent_session_scheduler status --json` — verify `worker_healthy` field present
- Verify `_check_worker_health()` is wrapped in try/except and cannot raise

### 3. Documentation

- **Task ID**: document-feature
- **Depends On**: validate-worker-health-check
- **Assigned To**: cli-health-builder
- **Agent Type**: builder
- **Parallel**: false
- Update or create `docs/features/session-management.md` with a section on worker health checks
- Add note to CLAUDE.md Quick Commands that `valor_session create` warns when no worker is running

### 4. Final Validation

- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: cli-health-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` — verify test suite passes
- Run `python -m ruff check .` — verify lint is clean
- Verify all success criteria in plan are met
- Generate final pass/fail report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| scheduler status has worker_healthy | `python -m tools.agent_session_scheduler status --json` | output contains worker_healthy |
| valor_session warns on missing file | `mv data/last_worker_connected data/last_worker_connected.bak 2>/dev/null; python -m tools.valor_session create --role pm --message "test" 2>&1 | grep -i "WARNING.*worker"; mv data/last_worker_connected.bak data/last_worker_connected 2>/dev/null` | stdout contains "WARNING" |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Simplifier | Task 3 references `documentarian` agent type not listed in Available Agent Types | Task 3 updated | Changed `Agent Type: documentarian` → `builder`, `Assigned To` → `cli-health-builder` |
| CONCERN | User | Verification table row 5 merely renames file and exits 0 — never actually tests the warning | Verification table updated | Replaced with real end-to-end command: rename file, run `valor_session create`, grep stdout for WARNING, restore file |
| CONCERN | Operator | Noisy stderr on repeated automated `status` polls when worker is unhealthy | Technical Approach + Task 1 + Failure Path updated | `cmd_status` plain-text warning now guarded by `if not args.json:` — automated `--json` callers receive no stderr noise |
| NIT | Skeptic | Technical Approach contradicts itself: "extract helper" vs "prefer duplicate" | Technical Approach clarified | Stated explicitly: helper lives in `valor_session.py`, 5-line inline duplicate in `agent_session_scheduler.py` — no cross-file import |
| NIT | Adversary | Plan uses `datetime.fromisoformat()` but dashboard uses `stat().st_mtime` | Technical Approach + Task 1 + Failure Path updated | Switched to `stat().st_mtime` throughout — simpler, no string parsing, consistent with `ui/app.py` pattern |

---

## Open Questions

None — the scope and approach are clear from the recon. The plan implements all three warning surfaces identified in the issue.
