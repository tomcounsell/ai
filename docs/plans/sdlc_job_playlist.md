---
status: Approved
type: feature
appetite: Medium
owner: Valor
created: 2026-03-20
tracking: https://github.com/tomcounsell/ai/issues/450
last_comment_id:
---

# SDLC Job Queue Playlist and Persona-Aware Scheduling

## Problem

The job scheduler tool (`tools/job_scheduler.py`) exists and works, but no persona knows about it, there is no way to queue multiple issues for sequential SDLC processing, and there are no persona-level restrictions on what can be scheduled.

**Current behavior:**
- The PM persona promised to schedule a job on Captain but never invoked the tool — personas have no knowledge of `job_scheduler.py`
- Scheduling multiple SDLC issues requires manually invoking `job_scheduler schedule --issue N` one at a time, with no sequential chaining
- The teammate persona can schedule SDLC jobs, which it shouldn't be able to do
- The summarizer treats the bare word "scheduled" as evidence of real action, without requiring a verifiable artifact like a job ID

**Desired outcome:**
- Playlist operations exposed via `job_scheduler.py` tool that agents invoke directly
- Observer automatically pops the next issue from the playlist when the current one completes
- Failed jobs get requeued to run after remaining items (only dependency/child failures block others)
- Persona-aware scheduling restrictions (teammate cannot schedule SDLC jobs)
- Each persona's soul file documents the job scheduler tool and its permissions
- Summarizer requires a verifiable artifact (job ID) not just the word "scheduled"

## Prior Art

No prior issues found related to playlist queuing or persona-aware scheduling. The building blocks were shipped in:
- **PR #390**: Async job queue with parent-child hierarchy (`agent/job_queue.py`)
- **Issue #359**: Summarizer evidence patterns (the "scheduled" pattern at `bridge/summarizer.py:497`)

## Data Flow

1. **Entry point**: Agent invokes `job_scheduler.py playlist --issues 440 445 397` (or user asks in natural language via Telegram, agent interprets and invokes the tool)
2. **Tool handler**: Validates all issue numbers via `gh issue view`, creates a Redis list `playlist:{project_key}` containing `[440, 445, 397]`, schedules the first issue via `job_scheduler.py schedule --issue 440`
3. **Job queue**: Processes the first SDLC job through the full pipeline (Observer steers stages)
4. **Observer completion hook**: When an SDLC job completes (session status -> completed), checks `playlist:{project_key}` in Redis. If non-empty, pops the next issue number and calls `job_scheduler.py schedule --issue N`
5. **Failure handling**: When an SDLC job fails, the failed issue is requeued at the end of the playlist and the next issue is popped. Only dependency failures (child jobs) block the parent from proceeding.
6. **Repeat**: Steps 3-5 repeat until the playlist is empty
7. **Output**: Each job's completion/failure is delivered to Telegram. Playlist exhaustion sends a final summary.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on persona restrictions)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites — all building blocks (job_scheduler, job_queue, observer, persona system) are already shipped.

## Solution

### Key Elements

- **Playlist data structure**: Redis list (`playlist:{project_key}`) holding ordered issue numbers
- **`playlist` subcommand in job_scheduler.py**: Agents invoke this tool directly to enqueue multiple issues; no Telegram slash commands (users speak naturally, agents interpret and invoke)
- **Observer playlist hook**: After-completion check that pops the next issue from the playlist and enqueues it
- **Failure requeue**: Failed jobs are appended to the end of the playlist and the next item is processed. Only dependency (child job) failures block the parent.
- **Persona gate in job_scheduler**: Reads `PERSONA` env var and rejects SDLC scheduling from teammate. Default is "developer" (permissive) when unset.
- **Persona soul file updates**: Document job scheduler tool and permissions in each overlay
- **Summarizer evidence hardening**: Require job ID pattern (e.g., `job-abc123`) alongside "scheduled"

### Flow

**User (natural language)** → **Agent interprets** → `job_scheduler.py playlist --issues 440 445 397` → **Tool validates issues** → **Redis playlist populated** → **First issue scheduled** → **Observer completes job** → **Playlist pop** → **Next issue scheduled** → ... → **Job fails** → **Requeue to end** → ... → **Playlist empty** → **Summary delivered**

### Technical Approach

- Playlist stored as a Redis list via `popoto` or raw Redis `LPUSH`/`RPOP` operations. Since `popoto` is the ORM used everywhere else, prefer a simple `PlaylistEntry` model or direct Redis list operations via the existing Redis connection.
- The Observer completion hook should be added in `agent/job_queue.py` where session status transitions to `completed`, not in `bridge/observer.py` — the observer makes steer/deliver decisions but doesn't own job lifecycle.
- **Failure requeue**: When a job transitions to `failed`, the Observer hook appends the issue number back to the end of the playlist (RPUSH) and pops the next item. A `retry_count` field on the playlist entry prevents infinite retry loops (max 1 retry per issue).
- Persona check uses `os.environ.get("PERSONA", "developer")` which is already set by `sdk_client.py` when spawning workers. Default "developer" is intentionally permissive.
- No Telegram slash commands — agents use `job_scheduler.py playlist` directly. Users communicate in natural language; the agent translates to tool invocations.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `job_scheduler.py` persona gate: test that rejection produces structured JSON error output, not a silent pass
- [ ] Playlist pop when Redis list is empty: verify no crash, returns cleanly
- [ ] Observer playlist hook when `gh issue view` fails for next issue: verify error is logged and reported, not swallowed

### Empty/Invalid Input Handling
- [ ] `job_scheduler.py playlist` with no `--issues` flag: returns usage help
- [ ] `job_scheduler.py playlist --issues 0 -1 abc`: returns validation error
- [ ] `job_scheduler.py playlist` with closed issues: skips or warns, does not enqueue
- [ ] Playlist with a mix of valid and invalid issues: enqueues valid ones, reports skipped ones

### Error State Rendering
- [ ] Persona rejection message is user-visible and explains why
- [ ] Playlist status command shows current position and remaining issues

## Test Impact

- [ ] `tests/unit/test_pending_recovery.py` — UPDATE: may need adjustment if job completion logic changes to include playlist hook
- No other existing tests directly cover the playlist or persona-gate features — this is largely greenfield.

## Rabbit Holes

- **Complex playlist management UI**: Tempting to build reorder, insert-at-position, priority-per-item features. Just support append and sequential pop. Anything fancier is a separate project.
- **Cross-project playlists**: Each playlist is scoped to a single project_key. Cross-project orchestration is out of scope.
- **Complex retry policies**: Failed items get one requeue to the end of the playlist. No configurable retry counts, backoff strategies, or per-issue retry policies.
- **Parallel playlist execution**: The playlist is sequential by design. Running multiple SDLC jobs in parallel would conflict on git operations.

## Risks

### Risk 1: Playlist state lost on bridge restart
**Impact:** Partially-processed playlist disappears, remaining issues never get scheduled.
**Mitigation:** Playlist is stored in Redis (persistent), not in-memory. On bridge restart, the Observer completion hook will pick up the playlist when the next job completes. Add a `playlist status` subcommand to inspect current state.

### Risk 2: Observer hook creates infinite scheduling loop
**Impact:** A bug in the playlist pop logic could schedule the same issue repeatedly.
**Mitigation:** Pop is destructive (RPOP removes the item). Add a guard: if the issue number being scheduled matches the just-completed issue, skip it. Rate limiting in `job_scheduler.py` (30/hour cap) provides a safety net.

## Race Conditions

### Race 1: Concurrent playlist modification during Observer pop
**Location:** Observer completion hook + `/sdlc-queue` command
**Trigger:** User appends to playlist while Observer is popping the next item
**Data prerequisite:** Redis list must exist and be non-empty
**State prerequisite:** One SDLC job is completing while user simultaneously adds to the playlist
**Mitigation:** Redis list operations (RPOP, LPUSH) are atomic. No application-level locking needed. The pop and push operate on opposite ends of the list.

## No-Gos (Out of Scope)

- Playlist reordering or priority-per-item (just sequential FIFO)
- Cross-project playlists
- Multiple retries of failed playlist items (max 1 requeue per issue)
- Parallel playlist execution
- Web UI for playlist management
- Playlist persistence across Redis flushes (standard Redis persistence handles this)

## Update System

No update system changes required — this feature is purely internal to the bridge and agent. No new dependencies, no new config files, no migration steps. The `/sdlc-queue` command is a Claude Code slash command that deploys with the repo.

## Agent Integration

- **Tool extension**: `job_scheduler.py` gains a `playlist` subcommand. Agents invoke this tool directly — no Telegram slash commands needed. Users speak naturally; the agent interprets intent and invokes the tool.
- **Bridge integration**: The Observer completion hook in `agent/job_queue.py` calls `job_scheduler.py schedule` directly (subprocess or import). No new MCP exposure needed since the bridge already has access.
- **Failure requeue**: Observer hook also handles `failed` status by requeuing the issue and popping the next.
- **Persona soul files**: `~/Desktop/Valor/personas/{developer,project-manager,teammate}.md` are updated with job scheduler documentation. These are read by `sdk_client.py` at worker spawn time.
- No new MCP servers or `.mcp.json` changes required.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/sdlc-job-playlist.md` describing the playlist feature, Observer hook, and persona restrictions
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Code comments on the Observer playlist hook explaining the pop-and-schedule flow
- [ ] Docstrings for the persona gate function in `job_scheduler.py`

## Success Criteria

- [ ] `job_scheduler.py playlist --issues 440 445` enqueues issues 440 and 445 sequentially; 440 starts immediately
- [ ] When issue 440's SDLC job completes, issue 445 is automatically scheduled
- [ ] When an SDLC job fails, the failed issue is requeued to end of playlist and next issue proceeds
- [ ] Only dependency (child job) failures block the parent from proceeding
- [ ] When the playlist is empty after the last job completes, a summary is delivered to Telegram
- [ ] `python -m tools.job_scheduler schedule --issue 113` from teammate persona returns a permission error
- [ ] `python -m tools.job_scheduler schedule --issue 113` from developer/PM persona works normally
- [ ] Each persona soul file documents the job scheduler tool and its specific permissions
- [ ] Summarizer evidence pattern for "scheduled" requires a job ID artifact (e.g., `job-abc123`)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (playlist-infra)**
  - Name: playlist-builder
  - Role: Implement Redis playlist, Observer hook, and `/sdlc-queue` command
  - Agent Type: builder
  - Resume: true

- **Builder (persona-gate)**
  - Name: persona-builder
  - Role: Implement persona check in job_scheduler and update soul files
  - Agent Type: builder
  - Resume: true

- **Builder (summarizer-fix)**
  - Name: summarizer-builder
  - Role: Harden summarizer evidence pattern for "scheduled"
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: playlist-validator
  - Role: Verify all success criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement playlist data structure and `playlist` subcommand
- **Task ID**: build-playlist
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_playlist.py (create)
- **Assigned To**: playlist-builder
- **Agent Type**: builder
- **Parallel**: true
- Create Redis list operations for playlist: `playlist_push(project_key, issue_numbers)`, `playlist_pop(project_key)`, `playlist_status(project_key)`, `playlist_requeue(project_key, issue_number)`
- Add `playlist` subcommand to `job_scheduler.py` with `--issues` flag for enqueueing multiple issues
- Validate issues via `gh issue view`, schedule the first issue via `job_scheduler.py schedule`
- Track retry count per issue (max 1 requeue on failure)

### 2. Implement Observer playlist hook
- **Task ID**: build-observer-hook
- **Depends On**: build-playlist
- **Validates**: tests/unit/test_sdlc_playlist.py::test_observer_hook (create)
- **Assigned To**: playlist-builder
- **Agent Type**: builder
- **Parallel**: false
- Add completion hook in `agent/job_queue.py` where session transitions to `completed`
- Hook checks `playlist_pop(project_key)` and if non-empty, calls `job_scheduler.py schedule --issue N`
- On job failure, requeue the failed issue to end of playlist (if retry_count < 1), then pop next
- On playlist exhaustion, deliver a summary message to Telegram via the session's `chat_id`

### 3. Implement persona gate in job_scheduler
- **Task ID**: build-persona-gate
- **Depends On**: none
- **Validates**: tests/unit/test_job_scheduler_persona.py (create)
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `_check_persona_permission(action_type)` function to `job_scheduler.py`
- Read persona from `os.environ.get("PERSONA", "developer")`
- Block SDLC scheduling (`cmd_schedule`) from teammate persona with structured JSON error
- Allow all personas to use `push`, `status`, `bump`, `pop`, `cancel`

### 4. Update persona soul files
- **Task ID**: build-persona-docs
- **Depends On**: build-persona-gate
- **Validates**: manual review
- **Assigned To**: persona-builder
- **Agent Type**: builder
- **Parallel**: false
- Add job scheduler tool documentation to `~/Desktop/Valor/personas/developer.md`
- Add job scheduler tool documentation to `~/Desktop/Valor/personas/project-manager.md`
- Add restricted job scheduler documentation to `~/Desktop/Valor/personas/teammate.md`

### 5. Harden summarizer evidence pattern
- **Task ID**: build-summarizer-fix
- **Depends On**: none
- **Validates**: tests/unit/test_summarizer.py (update existing)
- **Assigned To**: summarizer-builder
- **Agent Type**: builder
- **Parallel**: true
- Change the `r"\b(?:scheduled|queued)\b"` pattern in `bridge/summarizer.py:497` to require a job ID artifact
- New pattern: `r"\b(?:scheduled|queued)\b.*\bjob[_-]?[a-f0-9]{6,}"` or similar
- Ensure existing summarizer tests still pass with the tightened pattern

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-observer-hook, build-persona-gate, build-summarizer-fix
- **Assigned To**: playlist-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/sdlc-job-playlist.md`
- Add entry to `docs/features/README.md` index table

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: playlist-validator
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
| Playlist subcommand exists | `grep -q 'playlist' tools/job_scheduler.py` | exit code 0 |
| Persona gate function exists | `grep -q '_check_persona_permission' tools/job_scheduler.py` | exit code 0 |
| Summarizer pattern hardened | `grep -q 'job[_-]\?[a-f0-9]' bridge/summarizer.py` | exit code 0 |

---

## Open Questions

All resolved:

1. ~~**Playlist failure policy**~~: Failed jobs get requeued to end of playlist. Only dependency (child) failures block others. (Max 1 retry per issue to prevent loops.)

2. ~~**Playlist visibility**~~: Agents use the `job_scheduler.py playlist` tool directly. No Telegram slash commands — users speak naturally, agents interpret and invoke.

3. ~~**Persona env var reliability**~~: Default "developer" when `PERSONA` is unset is correct and intentionally permissive.
