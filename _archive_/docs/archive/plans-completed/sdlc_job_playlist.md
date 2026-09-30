---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/450
last_comment_id:
---

# SDLC Job Queue Playlist and Persona-Aware Scheduling

## Problem

The job scheduler tool (`tools/job_scheduler.py`) exists and works, but no persona knows about it, there's no way to queue multiple issues for sequential SDLC processing, and no persona-level restrictions on what can be scheduled.

**Current behavior:**
- PM on Captain promised to schedule a job but never invoked the tool — personas lack documentation about the scheduler
- To process multiple issues, each must be manually triggered one at a time
- Any persona can schedule any type of job, including teammate scheduling SDLC runs

**Desired outcome:**
- `/sdlc-queue 440 445 397 443` enqueues multiple issues for sequential SDLC processing
- Observer auto-pops the next issue from the playlist after each job completes
- Persona-aware restrictions prevent teammate from scheduling SDLC jobs
- Each persona's soul file documents what scheduling capabilities it has

## Prior Art

- **PR #390**: Job queue with parent-child hierarchy — shipped the `agent/job_queue.py` async queue and `models/agent_session.py` scheduling fields. Foundation for this work.
- **Issue #359**: Summarizer evidence patterns — relevant to the note about requiring verifiable artifacts (job IDs) not just the word "scheduled"

No prior issues found related to playlist queuing or persona-aware scheduling restrictions.

## Data Flow

1. **Entry point**: User sends `/sdlc-queue 440 445 397` in Telegram
2. **Bridge classifier**: Routes to the sdlc-queue command handler
3. **Playlist population**: Validates each issue number via `gh issue view`, pushes to Redis list `job_playlist:{project_key}`
4. **First job dispatch**: Pops issue #440 from playlist, calls `job_scheduler.py schedule --issue 440`
5. **SDLC pipeline**: Observer steers the job through Plan → Build → Test → Review → Docs → Merge
6. **Job completion**: Observer detects job completion via `_execute_job` completion handler
7. **Playlist check**: Completion handler checks `job_playlist:{project_key}` in Redis
8. **Next job**: If playlist non-empty, pops next issue and schedules it via `job_scheduler.py`
9. **Loop**: Steps 5-8 repeat until playlist is empty
10. **Output**: Telegram notification on each job completion and when playlist is exhausted

## Architectural Impact

- **New dependencies**: None — uses existing Redis, job_scheduler, and Observer
- **Interface changes**: New `playlist` subcommand in job_scheduler.py; new Observer hook in job completion path
- **Coupling**: Minimal — playlist is a simple Redis list checked at one point (job completion)
- **Data ownership**: Playlist owned by Redis, keyed per project. Observer reads, scheduler writes.
- **Reversibility**: Easy — remove the playlist check from completion handler, delete Redis keys

## Appetite

**Size:** Medium

**Team:** Solo dev, PM review

**Interactions:**
- PM check-ins: 1 (scope alignment on persona permissions)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis available | `python -c "import redis; redis.Redis().ping()"` | Playlist storage |
| job_scheduler.py exists | `test -f tools/job_scheduler.py` | Foundation tool |
| Persona files exist | `test -f ~/Desktop/Valor/personas/developer.md` | Soul file updates |

## Solution

### Key Elements

- **Playlist data structure**: Redis list (`job_playlist:{project_key}`) storing issue numbers in order
- **`/sdlc-queue` command**: Parses issue numbers, validates each, populates playlist, kicks off first job
- **Observer playlist hook**: After job completion, checks playlist and enqueues next issue
- **Persona gate in job_scheduler**: Reads `VALOR_PERSONA` env var, rejects SDLC scheduling from teammate
- **Soul file updates**: Document scheduler capabilities in each persona's overlay file

### Flow

**Telegram message** → `/sdlc-queue 440 445 397` → **Validate issues** → **Populate Redis list** → **Schedule first issue** → **SDLC pipeline runs** → **Job completes** → **Observer checks playlist** → **Pop next issue** → **Schedule it** → ... → **Playlist empty** → **Notify user**

### Technical Approach

- Playlist uses Redis LPUSH/RPOP (FIFO order) with key `job_playlist:{project_key}`
- The `/sdlc-queue` command is a new subcommand in `tools/job_scheduler.py` (not a separate tool)
- Observer hook lives in the job completion path in `agent/job_queue.py` — after `_execute_job` completes successfully, check playlist
- Persona check is a simple env var read at the top of `cmd_schedule()` — `VALOR_PERSONA` is already set by `sdk_client.py` when spawning subprocesses
- Summarizer evidence pattern update: require job ID in output to count "scheduled" as evidence

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `cmd_playlist()` — test with invalid issue numbers (non-existent, closed), empty list, mixed valid/invalid
- [ ] Observer playlist hook — test with Redis connection failure (should not block job completion)
- [ ] Persona gate — test rejection from teammate, acceptance from developer and PM

### Empty/Invalid Input Handling
- [ ] `/sdlc-queue` with no arguments → helpful error message
- [ ] `/sdlc-queue` with all invalid issues → no playlist created, clear error listing each failure
- [ ] Playlist pop on empty list → no-op, no crash

### Error State Rendering
- [ ] Persona rejection produces clear Telegram message explaining the restriction
- [ ] Playlist creation confirms which issues were accepted/rejected

## Test Impact

- [ ] `tests/integration/test_job_scheduler.py` — UPDATE: add tests for new `playlist` subcommand and persona gate
- [ ] `tests/unit/test_summarizer.py` — UPDATE: update evidence patterns if "scheduled" verification changes

No other existing tests affected — playlist hook and persona gate are additive features with new test files.

## Rabbit Holes

- **Complex playlist management UI**: Don't build reorder/insert/priority per-item — simple FIFO is enough
- **Cross-project playlists**: Each playlist is per-project. Don't build global playlists.
- **Playlist persistence/recovery**: If the bridge restarts, the Redis list survives. Don't add filesystem backup.
- **Parallel SDLC runs from playlist**: Issues run sequentially, not parallel. Parallel is a separate concern.

## Risks

### Risk 1: Observer merge conflicts block playlist hook
**Impact:** Can't add the playlist check to the completion path because `bridge/observer.py` has 8 active merge conflicts
**Mitigation:** Resolve observer.py merge conflicts first (prerequisite task), or place the hook in `agent/job_queue.py` completion path instead

### Risk 2: Persona env var not propagated correctly
**Impact:** Persona gate doesn't fire because `VALOR_PERSONA` isn't set when job_scheduler runs
**Mitigation:** Verify env var propagation in `sdk_client.py` subprocess spawning; add fallback to read from session context

## Race Conditions

### Race 1: Concurrent playlist pops
**Location:** Observer playlist hook in job completion path
**Trigger:** Two jobs completing simultaneously for the same project (unlikely with sequential worker, but possible with scheduled jobs)
**Data prerequisite:** Playlist must have items
**State prerequisite:** Only one pop should happen per completion
**Mitigation:** Use Redis RPOP which is atomic — two concurrent pops get two different items, no duplication. If playlist has one item, one gets it, the other gets nil.

### Race 2: Playlist population during active SDLC run
**Location:** `/sdlc-queue` command writing to playlist while Observer is reading it
**Trigger:** User adds more issues to playlist while a job is running
**Data prerequisite:** Existing playlist
**State prerequisite:** N/A
**Mitigation:** Redis list operations (LPUSH, RPOP) are atomic. New items append to the end. Running job is unaffected.

## No-Gos (Out of Scope)

- Playlist reordering or priority per-item (simple FIFO only)
- Pause/resume playlist (cancel individual items or clear all is enough)
- Cross-project or global playlists
- Parallel SDLC execution from playlist
- Playlist status dashboard or web UI

## Update System

No update system changes required — this feature uses existing Redis and job_scheduler infrastructure. The persona files are already synced via iCloud (~/Desktop/Valor/personas/). No new dependencies or config files to propagate.

## Agent Integration

- **job_scheduler.py** is already a CLI tool invokable by the agent — the new `playlist` subcommand will be automatically available
- No new MCP server needed — job_scheduler is invoked via `python -m tools.job_scheduler`
- The bridge (`agent/job_queue.py`) needs the playlist completion hook — this is bridge-internal code
- Persona soul files (~/Desktop/Valor/personas/*.md) will document the tool so personas know to use it
- Integration test: verify agent can run `python -m tools.job_scheduler playlist --issues 1 2 3` and see the playlist populated

## Documentation

- [ ] Create `docs/features/sdlc-job-playlist.md` describing the playlist feature, usage, and persona restrictions
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update persona soul files with job scheduler documentation (developer, PM, teammate)

## Success Criteria

- [ ] `/sdlc-queue 440 445` creates a Redis playlist and schedules issue #440
- [ ] After issue #440 completes, Observer auto-schedules issue #445 from the playlist
- [ ] When playlist is exhausted, Telegram notification sent
- [ ] `python -m tools.job_scheduler playlist --issues 440 445` works from CLI
- [ ] Teammate persona is rejected when attempting `schedule` subcommand
- [ ] Developer and PM personas can use `schedule` freely
- [ ] Each persona soul file documents its scheduling capabilities
- [ ] Summarizer evidence pattern requires job ID for "scheduled" evidence
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (playlist)**
  - Name: playlist-builder
  - Role: Implement playlist data structure, job_scheduler subcommand, and Observer hook
  - Agent Type: builder
  - Resume: true

- **Builder (persona-gate)**
  - Name: persona-gate-builder
  - Role: Implement persona check in job_scheduler and update soul files
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end playlist flow and persona restrictions
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs and update index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Resolve observer.py merge conflicts (prerequisite)
- **Task ID**: fix-observer-conflicts
- **Depends On**: none
- **Validates**: `python -c "import bridge.observer"` succeeds without SyntaxError
- **Assigned To**: playlist-builder
- **Agent Type**: builder
- **Parallel**: true
- Resolve all 8 merge conflict markers in `bridge/observer.py`
- Ensure observer imports and basic functionality work

### 2. Implement playlist data structure and CLI command
- **Task ID**: build-playlist
- **Depends On**: none
- **Validates**: tests/unit/test_job_scheduler_playlist.py (create)
- **Assigned To**: playlist-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `cmd_playlist()` to `tools/job_scheduler.py` with `--issues` argument
- Use Redis LPUSH for populating, RPOP for consuming
- Key format: `job_playlist:{project_key}`
- Add `playlist-status` subcommand to show current playlist contents
- Add `playlist-clear` subcommand to clear a project's playlist
- Validate each issue exists and is open before adding to playlist
- Schedule the first issue immediately after populating

### 3. Add Observer playlist hook in job completion
- **Task ID**: build-observer-hook
- **Depends On**: fix-observer-conflicts, build-playlist
- **Validates**: tests/integration/test_playlist_completion.py (create)
- **Assigned To**: playlist-builder
- **Agent Type**: builder
- **Parallel**: false
- In `agent/job_queue.py`, after successful job completion, check `job_playlist:{project_key}`
- If non-empty, RPOP next issue number and call `cmd_schedule()` programmatically
- Send Telegram notification: "Playlist: completed issue #X, starting issue #Y (Z remaining)"
- When playlist is empty, send: "Playlist complete — all N issues processed"
- Wrap in try/except — playlist failure must never block job completion

### 4. Implement persona gate in job_scheduler
- **Task ID**: build-persona-gate
- **Depends On**: none
- **Validates**: tests/unit/test_persona_scheduling.py (create)
- **Assigned To**: persona-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- Read `VALOR_PERSONA` env var in `cmd_schedule()` (default: "developer")
- If persona is "teammate", reject with clear error: "Teammate persona cannot schedule SDLC jobs. Use developer or PM persona."
- Allow all other subcommands (push, status, etc.) from any persona
- Verify `VALOR_PERSONA` is set in `sdk_client.py` subprocess environment

### 5. Update persona soul files
- **Task ID**: build-persona-docs
- **Depends On**: build-persona-gate
- **Validates**: grep confirms job_scheduler documented in each persona file
- **Assigned To**: persona-gate-builder
- **Agent Type**: builder
- **Parallel**: false
- Add job scheduler section to `~/Desktop/Valor/personas/developer.md`
- Add job scheduler section to `~/Desktop/Valor/personas/project-manager.md`
- Add restricted job scheduler section to `~/Desktop/Valor/personas/teammate.md`
- Developer and PM: document schedule, push, status, playlist commands
- Teammate: document push and status only, explicitly note SDLC restriction

### 6. Update summarizer evidence pattern
- **Task ID**: build-summarizer-update
- **Depends On**: none
- **Validates**: tests/unit/test_summarizer.py (update)
- **Assigned To**: persona-gate-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/summarizer.py`, update the "scheduled/queued" evidence pattern to require a job ID
- Pattern should match: "scheduled job abc123" or "queued job-id: xyz789" but not bare "scheduled"

### 7. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-observer-hook, build-persona-gate, build-summarizer-update
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify playlist Redis operations work end-to-end
- Verify persona gate rejects teammate SDLC scheduling
- Verify summarizer evidence pattern change
- Run full test suite

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-job-playlist.md`
- Add entry to `docs/features/README.md` index table
- Update inline code comments

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
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
| Observer imports | `python -c "import bridge.observer"` | exit code 0 |
| Playlist CLI works | `python -m tools.job_scheduler playlist --help` | exit code 0 |
| Persona gate exists | `grep -q "VALOR_PERSONA" tools/job_scheduler.py` | exit code 0 |
| Persona docs updated | `grep -q "job_scheduler" ~/Desktop/Valor/personas/developer.md` | exit code 0 |

---

## Open Questions

1. **Playlist notifications**: Should each playlist job completion send a Telegram message, or only when the entire playlist finishes? (Proposed: both — per-job status + final summary)
2. **Playlist append**: Should `/sdlc-queue` append to an existing playlist or replace it? (Proposed: append, with `playlist-clear` to reset)
3. **Observer merge conflicts**: The observer.py has 8 active merge conflicts. Should resolving these be a prerequisite for this issue, or should we file a separate issue? (Proposed: resolve as Task 1 of this plan since the hook needs clean observer code)
