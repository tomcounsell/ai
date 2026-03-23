---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-03-23
tracking: https://github.com/tomcounsell/ai/issues/479
---

# Job Scheduler Kill Command

## Problem

When a running agent job needs to be stopped (stuck agent, wrong persona, debugging), there is no clean kill path. The only option is manually deleting Redis entries and restarting the bridge.

**Current behavior:**
`python -m tools.job_scheduler cancel --job-id <id>` only works for `status="pending"` jobs (hardcoded filter at `job_scheduler.py:660`). Running jobs are invisible. Manually deleting the Redis entry triggers the recovery system (`_recover_interrupted_jobs`) to recreate the job as pending with high priority on the next restart -- zombie jobs keep coming back.

**Desired outcome:**
A single CLI command that terminates any job (pending or running), kills the subprocess, and ensures it never gets re-enqueued.

## Prior Art

- **Issue #127**: Job queue: detect and recover stuck running jobs -- resulted in the current health check machinery (`_check_and_recover_stuck_jobs`). Recovery always re-enqueues; no kill path was added.
- **Issue #402**: Watchdog stall recovery for pending sessions never kills stuck worker -- improved stall detection but focused on recovery, not intentional kills.
- **Issue #440**: Session watchdog failures -- fixed Redis key errors and SDK timeouts, no kill command.

## Data Flow

1. **Entry point**: User runs `python -m tools.job_scheduler kill --job-id <id>`
2. **Redis lookup**: Find `AgentSession` by job_id (any status)
3. **Process kill**: Use `pgrep -f` matching session_id to find Claude CLI subprocess, send SIGTERM
4. **Redis cleanup**: Set `status="killed"` on the AgentSession (preserves observability)
5. **Output**: JSON report with job_id, session_id, PID killed, and final status

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: New `kill` subcommand in job_scheduler CLI, new `"killed"` status value
- **Coupling**: No new coupling -- uses existing `pgrep` pattern from `_cleanup_orphaned_claude_processes`
- **Data ownership**: No change -- job_scheduler already manages AgentSession records
- **Reversibility**: Trivial -- remove the subcommand and status value

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **`cmd_kill` function**: New subcommand handler in `tools/job_scheduler.py` that kills by job_id, session_id, or all
- **`status="killed"`**: New terminal status that all recovery functions skip
- **Process termination**: Direct `os.kill()` using PIDs from `pgrep -f` matching session_id

### Flow

**CLI invocation** -> Find job in Redis (any status) -> Find subprocess via pgrep -> SIGTERM subprocess -> Set status="killed" -> Report result

### Technical Approach

- Extend `cmd_cancel` pattern: look up job across all statuses, not just pending
- Find subprocess PID: `pgrep -f "session_id"` (the SDK client passes session_id as CLI args, making it greppable)
- Kill sequence: SIGTERM, wait 3s, SIGKILL if still alive
- Set `status="killed"` via delete-and-recreate (same pattern as `_recover_interrupted_jobs` to avoid Popoto index corruption)
- Update recovery functions to skip `status="killed"`: `_recover_interrupted_jobs` (line 638), `_check_and_recover_stuck_jobs` (line 819), `_reset_running_jobs` (line 667)
- Add `--all` flag that kills all running + pending jobs

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `cmd_kill` must handle: job not found, subprocess not found (already dead), SIGTERM permission denied, Redis connection failure
- [ ] Each error path returns a structured JSON error (same pattern as `cmd_cancel`)

### Empty/Invalid Input Handling
- [ ] `--job-id ""` returns clear error
- [ ] `--job-id` with nonexistent ID returns "not found" (not crash)
- [ ] `--all` with no jobs returns "nothing to kill" (not error)

### Error State Rendering
- [ ] Kill result always includes job_id, session_id, status, and pid (or "no process found")

## Test Impact

- [ ] `tests/unit/test_job_scheduler_persona.py` -- no changes needed (tests persona restrictions, kill is unrestricted)

No existing tests directly affected -- `cmd_cancel` tests (if any) remain valid since cancel still works for pending jobs. Kill is additive.

## Rabbit Holes

- Bridge-cooperative kill via pub/sub or signal files -- overkill, direct process kill + Redis update is sufficient
- Adding a web UI or dashboard for job management -- separate concern
- Graceful drain (wait for current task to finish) -- different from kill; kill means stop now

## Risks

### Risk 1: Killing the wrong subprocess
**Impact:** Could terminate an unrelated Claude CLI process
**Mitigation:** Match on session_id in the process args (unique per job), not just "claude" binary name. Verify PID is a child of the bridge or orphaned (PPID=1) before killing.

## Race Conditions

### Race 1: Kill during job transition
**Location:** `_pop_job` in `agent/job_queue.py:370`
**Trigger:** Kill command runs while `_pop_job` is deleting and recreating the job (pending->running transition)
**Data prerequisite:** Job must exist in Redis
**State prerequisite:** The delete-and-recreate in `_pop_job` is not atomic
**Mitigation:** Kill command retries lookup once after 1s if job not found (covers the brief window during transition). If still not found, report "not found."

## No-Gos (Out of Scope)

- Graceful shutdown of a job (wait for current tool call to finish)
- Job pause/resume functionality
- Kill from Telegram (agent-initiated kill) -- CLI only for now
- Automatic kill on health check failure (existing recovery handles this differently)

## Update System

No update system changes required -- this is a CLI tool change with no new dependencies or config files.

## Agent Integration

No agent integration required -- the kill command is a human-operated CLI tool. The agent does not need to kill its own jobs. If needed in the future, it could be exposed via the job_scheduler MCP server.

## Documentation

- [ ] Update `CLAUDE.md` quick commands table with `kill` subcommand
- [ ] Add kill examples to `docs/tools-reference.md` if it documents job_scheduler

## Success Criteria

- [ ] `python -m tools.job_scheduler kill --job-id <id>` terminates a running job's subprocess
- [ ] `python -m tools.job_scheduler kill --job-id <id>` works on pending jobs (deletes without process kill)
- [ ] `python -m tools.job_scheduler kill --all` terminates all running and pending jobs
- [ ] Killed jobs show `status="killed"` in `python -m tools.job_scheduler status`
- [ ] Bridge restart does NOT re-enqueue killed jobs
- [ ] `grep -n '"killed"' agent/job_queue.py | wc -l` shows recovery functions skip killed status
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (kill-command)**
  - Name: kill-builder
  - Role: Implement cmd_kill, update recovery functions, add killed status
  - Agent Type: builder
  - Resume: true

- **Validator (kill-command)**
  - Name: kill-validator
  - Role: Verify kill works on running, pending, and already-dead jobs
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement kill command and killed status
- **Task ID**: build-kill
- **Depends On**: none
- **Validates**: tests/unit/test_job_scheduler_kill.py (create)
- **Assigned To**: kill-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `cmd_kill(args)` to `tools/job_scheduler.py` with `--job-id`, `--session-id`, and `--all` flags
- Add `"kill"` subparser to `main()` argument parser
- Implement process lookup via `pgrep -f` matching session_id
- Implement SIGTERM -> wait 3s -> SIGKILL sequence
- Use delete-and-recreate with `status="killed"` (Popoto pattern)
- Update `_recover_interrupted_jobs` to filter out `status="killed"` (line 638)
- Update `_check_and_recover_stuck_jobs` to filter out `status="killed"` (line 819)
- Update `_reset_running_jobs` to filter out `status="killed"` (line 667)
- Include killed jobs in `cmd_status` output (new section)
- Create unit tests for cmd_kill (job not found, pending kill, running kill, --all)

### 2. Validate kill command
- **Task ID**: validate-kill
- **Depends On**: build-kill
- **Assigned To**: kill-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `kill --job-id` with nonexistent ID returns structured error
- Verify recovery functions have killed-status skip logic
- Verify `status` command shows killed jobs
- Run full test suite

### 3. Documentation
- **Task ID**: document-kill
- **Depends On**: validate-kill
- **Assigned To**: kill-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update CLAUDE.md quick commands table
- Update docs/tools-reference.md if applicable

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-kill
- **Assigned To**: kill-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Kill subcommand exists | `python -m tools.job_scheduler kill --help` | exit code 0 |
| Recovery skips killed | `grep -c '"killed"' agent/job_queue.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- scope is narrow and well-defined from today's debugging session.
