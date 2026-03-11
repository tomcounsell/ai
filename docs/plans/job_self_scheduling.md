---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-03-11
tracking: https://github.com/tomcounsell/ai/issues/258
---

# Job Self-Scheduling: Agent-Initiated Queue Operations

## Problem

The job queue is production-grade for single-message → single-session flows, but the agent has no way to programmatically schedule work. This creates three pain points:

**1. No self-scheduling:** An agent discovers a bug mid-conversation but can't enqueue an SDLC run for it — it must ask the human to send a new message.

**2. No batch dispatch:** A human says "handle issues #111, #112, #113" and the agent can only work on one at a time inline, losing the others.

**3. No deferred execution:** Everything runs immediately. There's no "run this tonight" or "schedule SDLC for this issue after the current build finishes."

**Current behavior:**
Jobs only enter the queue through `bridge/telegram_bridge.py` when a Telegram message arrives. The `enqueue_job()` function in `agent/job_queue.py` is internal — not exposed as a tool the agent can call.

**Desired outcome:**
The agent can call a `schedule_job` tool mid-conversation to enqueue SDLC runs for GitHub issues, with optional priority and deferred scheduling. Batch dispatch is just multiple `schedule_job` calls. Queue status is observable from Telegram via a `/queue-status` skill.

## Prior Art

- **PR #95**: Fix job queue losing enqueued jobs — established the delete-and-recreate pattern for Popoto KeyField index integrity
- **PR #128**: Job health monitor — added stuck job detection and recovery
- **PR #284**: SDLC session tracking — classifier type + auto-continue propagation
- **PR #286**: AgentSession as single source of truth for auto-continue
- **PR #321**: Observer Agent — replaced auto-continue/summarizer with stage-aware SDLC steerer
- **PR #337**: Correlation IDs for end-to-end request tracing
- **PR #344**: Fix session stuck in pending after BUILD COMPLETED
- **PR #346**: Goal gates for SDLC pipeline stage enforcement

All succeeded. The queue infrastructure is mature — this plan extends it, not rebuilds it.

## Data Flow

### Current: Telegram → Queue → Worker
1. **Telegram message** arrives in `bridge/telegram_bridge.py`
2. **Bridge** calls `enqueue_job()` with chat_id, message_text, sender info
3. **Worker** pops job, creates `AgentSession`, runs via SDK
4. **Observer** steers auto-continue or delivers to Telegram

### New: Agent Tool → Queue → Worker → GitHub
1. **Agent** calls `schedule_job` tool mid-conversation (via bridge-internal tool, not MCP)
2. **Bridge tool handler** calls `enqueue_job()` with synthetic session params
3. **Worker** pops job, creates `AgentSession` with `classification_type="sdlc"`
4. **Observer** steers as normal; output stays in **AgentSession records and historical logs** (no Telegram delivery for headless jobs)
5. **GitHub issue** gets a progress comment via the Observer's existing link tracking

## Architectural Impact

- **New dependency**: None — uses existing `enqueue_job()` and GitHub CLI
- **Interface changes**: New `scheduled_after` field on AgentSession; new tool in `tools/` directory
- **Coupling**: Low — tool is a thin wrapper around existing `enqueue_job()`
- **Data ownership**: No change — AgentSession remains the single source of truth
- **Reversibility**: High — tool can be removed without affecting core queue

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope confirmation on headless output routing)
- Review rounds: 1 (code review)

The queue infrastructure is done. This is plumbing new entry points into it plus a small `scheduled_after` filter in `_pop_job()`.

## Prerequisites

No prerequisites — this work uses only existing Redis, GitHub CLI, and internal Python APIs.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "import redis; redis.Redis().ping()"` | Queue storage |
| GitHub CLI | `gh auth status` | Issue validation |

## Solution

### Key Elements

- **`schedule_job` tool**: A Python tool in `tools/` that the agent calls to enqueue SDLC work for a GitHub issue
- **`scheduled_after` field**: Optional datetime on AgentSession; `_pop_job()` skips jobs where `scheduled_after > now()`
- **`/queue-status` skill**: Telegram-accessible wrapper around existing CLI observability

### Flow

**Agent mid-conversation** → calls `schedule_job(issue=113, priority="normal")` → tool validates issue exists → calls `enqueue_job()` with synthetic params → returns job ID + queue position → worker picks up when ready → output persists in AgentSession logs

### Technical Approach

- **Tool, not MCP server**: The agent runs inside Claude Code which has access to `tools/` via Bash. A Python tool (`tools/job_scheduler.py`) is simpler than standing up an MCP server. The agent calls it via `python -m tools.job_scheduler schedule --issue 113 --priority normal`.
- **Synthetic session params**: The tool needs `project_key`, `chat_id`, `message_id` for context. These come from the current session context (passed as CLI args or env vars by the bridge). Headless jobs persist output to AgentSession logs only — no Telegram delivery.
- **`scheduled_after`**: Add field to AgentSession model. Single-line check in `_pop_job()`: `if job.scheduled_after and job.scheduled_after > datetime.now(UTC): continue`.
- **Batch = loop**: "Handle #111, #112, #113" is just the agent calling `schedule_job` three times. No special batch API needed.
- **Queue management skill**: A `.claude/commands/queue-status.md` skill that provides full queue inspection AND manipulation: view status, bump jobs to top, push new jobs, pop/cancel jobs.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `schedule_job` tool: test with invalid issue number (closed, nonexistent) → returns clear error message
- [ ] `schedule_job` tool: test with Redis down → returns error, does not silently drop job
- [ ] `_pop_job` with `scheduled_after`: test that future-dated jobs are skipped, not lost

### Empty/Invalid Input Handling
- [ ] Empty issue number → immediate validation error
- [ ] Issue with no body/title → still schedules (body is optional context)
- [ ] `scheduled_after` in the past → treated as immediate (no error)

### Error State Rendering
- [ ] Tool returns structured JSON: `{"status": "queued", "job_id": "...", "queue_position": N}` on success
- [ ] Tool returns structured JSON: `{"status": "error", "message": "..."}` on failure
- [ ] Agent sees the error and can report it to the user

## Rabbit Holes

- **Full MCP server**: Standing up a new MCP server with stdio transport, manifest, registration in `.mcp.json` etc. is overkill. A CLI tool the agent calls via Bash is simpler and sufficient. MCP can come later if other consumers need it.
- **Headless sessions without Telegram routing**: Tempting to build sessions that only post to GitHub, but the Observer already handles output routing. Just route self-scheduled job output back to the originating Telegram thread.
- **Job dependencies / DAGs**: "Run B after A finishes" is a DAG scheduler. Sequential processing + priority already gives ordering. Don't build a DAG engine.
- **Parallel workers**: The issue explicitly decided concurrency=1. Don't revisit.

## Risks

### Risk 1: Session context unavailable when tool runs
**Impact:** Tool can't determine `project_key` or `chat_id` to route output back
**Mitigation:** Bridge already injects `CHAT_ID`, `PROJECT_KEY` etc. as env vars in the Claude Code subprocess. Tool reads from env. Test by checking env vars are populated in agent sessions.

### Risk 2: Self-scheduling loops
**Impact:** Agent schedules a job that schedules more jobs infinitely
**Mitigation:** Each `schedule_job` call logs the originating `correlation_id`. Depth limit: a scheduled job can schedule further jobs, but max chain depth = 3 (tracked via `scheduling_depth` field on AgentSession). Hard limit: max 30 scheduled jobs per hour per project. No human intervention required — the system self-regulates via depth and rate caps.

## Race Conditions

### Race 1: Job scheduled while worker is in drain-guard sleep
**Location:** `agent/job_queue.py` `_worker_loop` drain guard
**Trigger:** Tool enqueues job during the 0.1s drain guard sleep
**Data prerequisite:** Job must be in Redis index before worker re-checks
**State prerequisite:** Worker must be alive
**Mitigation:** Already handled — drain guard re-checks pending jobs after sleep. `_ensure_worker()` also restarts a dead worker.

### Race 2: `scheduled_after` check vs. system clock skew
**Location:** `_pop_job()` new scheduled_after filter
**Trigger:** Clock drift between when the tool writes `scheduled_after` and when worker reads it
**Data prerequisite:** `scheduled_after` must be a UTC datetime
**State prerequisite:** System clock must be reasonably accurate
**Mitigation:** Always use UTC. Acceptable margin: jobs may run up to 60s early/late due to worker poll interval. Not a real problem.

## No-Gos (Out of Scope)

- No MCP server — CLI tool via Bash is sufficient for now
- No parallel workers — sequential-only by design
- No job dependency DAGs — priority + sequential ordering is enough
- No Telegram delivery for headless jobs — output persists in AgentSession logs and session history only
- No Celery/RQ — Popoto ORM handles everything
- No GitHub progress comments for this iteration — Observer link tracking is sufficient

## Update System

No update system changes required. The new tool is a Python file in `tools/` and a new skill in `.claude/commands/`. Both propagate via `git pull`. No new system dependencies.

## Agent Integration

- **New tool**: `tools/job_scheduler.py` — CLI tool callable via `python -m tools.job_scheduler`
- **No MCP registration needed** — agent calls it via Bash, which is already permitted
- **Bridge env vars**: Verify that `CHAT_ID`, `PROJECT_KEY`, `MESSAGE_ID`, `SESSION_ID` are available in agent subprocess env
- **Integration test**: Agent calls `schedule_job`, job appears in Redis queue, worker picks it up, output routes to correct chat

## Documentation

- [ ] Create `docs/features/job-scheduling.md` covering the `schedule_job` tool usage and `scheduled_after` behavior
- [ ] Update `docs/features/job-queue.md` with the new `scheduled_after` field
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/tools-reference.md` with `job_scheduler` tool

## Success Criteria

- [ ] Agent can call `python -m tools.job_scheduler schedule --issue 113` and job appears in queue
- [ ] Agent can call `python -m tools.job_scheduler schedule --issue 113 --after "2026-03-12T02:00:00Z"` for deferred execution
- [ ] `_pop_job()` skips jobs with future `scheduled_after`
- [ ] Output from self-scheduled jobs persists in AgentSession logs (no Telegram delivery)
- [ ] Queue manipulation works: bump, push, pop subcommands function correctly
- [ ] `/queue-status` skill shows queued/running/completed jobs in Telegram-friendly format
- [ ] Self-scheduling depth cap: jobs at depth 3 cannot schedule further jobs (rate limit: 30/hr/project)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (scheduler-tool)**
  - Name: scheduler-builder
  - Role: Implement `tools/job_scheduler.py` CLI tool and `scheduled_after` field
  - Agent Type: builder
  - Resume: true

- **Builder (queue-status-skill)**
  - Name: skill-builder
  - Role: Create `/queue-status` skill wrapping existing CLI
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end: tool → queue → worker → Telegram output
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using: builder (2), validator (1)

## Step by Step Tasks

### 1. Add `scheduled_after` field to AgentSession
- **Task ID**: build-scheduled-after
- **Depends On**: none
- **Assigned To**: scheduler-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `scheduled_after` field to AgentSession model in `models/agent_session.py`
- Add to `_JOB_FIELDS` list in `agent/job_queue.py`
- Modify `_pop_job()` to skip jobs where `scheduled_after > now()`
- Modify `_extract_job_fields()` to preserve the field
- Add `scheduled_after` parameter to `enqueue_job()`
- Change `enqueue_job()` default priority from `"high"` to `"normal"`
- Update reflection scripts to enqueue with `priority="low"`

### 2. Implement `tools/job_scheduler.py`
- **Task ID**: build-scheduler-tool
- **Depends On**: build-scheduled-after
- **Assigned To**: scheduler-builder
- **Agent Type**: builder
- **Parallel**: false
- CLI with subcommands: `schedule`, `status`, `cancel`, `bump`, `push`, `pop`
- `schedule`: validates issue via `gh issue view`, calls `enqueue_job()` with synthetic params
- `bump`: move a pending job to top of queue (set priority=high, reset created_at to now)
- `push`: enqueue arbitrary message text as a job (not issue-bound)
- `pop`: remove next pending job from queue without executing it
- Reads `CHAT_ID`, `PROJECT_KEY`, `SESSION_ID` from env vars
- Returns structured JSON response
- Self-scheduling depth tracking: increment `scheduling_depth` from parent session (cap at 3)

### 3. Create `/queue-status` skill
- **Task ID**: build-queue-status
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `.claude/commands/queue-status.md`
- Provides queue inspection AND manipulation via `python -m tools.job_scheduler`
- **Inspect**: queued count, running job details, recent completions, per-job detail
- **Manipulate**: bump job to top, push new job, pop/cancel job, change priority

### 4. Verify env vars available in agent sessions
- **Task ID**: verify-env-vars
- **Depends On**: build-scheduler-tool
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Check `agent/sdk_client.py` for env var injection into subprocess
- Add any missing vars (`CHAT_ID`, `PROJECT_KEY`, `MESSAGE_ID`, `SESSION_ID`)
- Verify tool can read them

### 5. Integration test
- **Task ID**: test-integration
- **Depends On**: verify-env-vars, build-queue-status
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Test: `schedule_job` → job in Redis → worker picks up → output routes correctly
- Test: `scheduled_after` in future → job skipped until time passes
- Test: self-scheduling protection works
- Test: `/queue-status` shows expected output

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: test-integration
- **Assigned To**: scheduler-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/job-scheduling.md`
- Update `docs/features/job-queue.md`
- Update `docs/features/README.md` index
- Update `docs/tools-reference.md`

### 7. Production Test: SDLC scheduled job
- **Task ID**: prod-test-sdlc
- **Depends On**: test-integration
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Create a GitHub issue: "Update docs/features/job-queue.md to document the new priority model (urgent/high/normal/low)"
- From a running agent session, call `python -m tools.job_scheduler schedule --issue <N>` to enqueue it
- Verify: job appears in queue at `normal` priority
- Verify: worker picks it up, runs full SDLC (plan → build → test → review → docs → merge)
- Verify: AgentSession logs capture full pipeline output
- Verify: PR gets opened, changes are correct, docs updated
- Clean up: close the test issue after verifying

### 8. Production Test: Q&A scheduled job
- **Task ID**: prod-test-qa
- **Depends On**: test-integration
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: true (can run alongside prod-test-sdlc)
- From a running agent session, call `python -m tools.job_scheduler push --message "Kevin asks: what's the current architecture of the job queue system?" --project valor`
- Verify: job enqueues at `normal` priority with no issue link
- Verify: worker picks it up, agent produces a coherent answer
- Verify: AgentSession logs contain the Q&A response
- Verify: no SDLC pipeline triggered (non-SDLC classification)

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: prod-test-sdlc, prod-test-qa, document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Verify both production tests passed
- Verify documentation created

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Tool exists | `python -m tools.job_scheduler --help` | exit code 0 |
| Skill exists | `test -f .claude/commands/queue-status.md` | exit code 0 |
| Feature docs | `test -f docs/features/job-scheduling.md` | exit code 0 |

---

## Decisions (Resolved)

1. **Output routing**: Headless job output stays in AgentSession records and historical logs only — no Telegram delivery. Humans inspect via `/queue-status` or CLI.
2. **Rate limiting**: 30 scheduled jobs per hour per project.
3. **Queue manipulation**: `/queue-status` skill provides full inspection AND manipulation — bump to top, push, pop, cancel. Not just read-only.
4. **Priority levels**: Four tiers — `urgent > high > normal > low`. Everything defaults to `normal` — Telegram messages, self-scheduled jobs, all of it. The agent decides when to bump priority based on message content and context (e.g., production outage → urgent, routine feature request → normal). Reflection/maintenance tasks default to `low`. The system is trusted to triage intelligently — sometimes its own background work is more important than what a human just asked for. Sort key in `_pop_job()` updated from binary to 4-level: `{"urgent": 0, "high": 1, "normal": 2, "low": 3}`.
