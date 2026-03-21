---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-21
tracking: https://github.com/tomcounsell/ai/issues/459
last_comment_id:
---

# SDLC Redesign: Job/Session Split, Single-Session Pipeline, Observer Simplification

## Problem

A Telegram message like "SDLC issue 123" passes through 12+ components before producing a result: Telegram handler → routing classifier → intent classifier → session manager → Redis queue → worker → re-classifier → CWD switch → message enricher → Claude Code spawn → /sdlc skill → sub-skill → Observer (4-phase LLM) → re-enqueue → repeat 7x → summarizer → formatter → Telegram.

**Current behavior:**
1. Intent is classified twice (routing.py and sdk_client.py) — these can disagree
2. `AgentSession` conflates the human's request with the Claude Code execution — no clean Job/Session separation. The delete-and-recreate pattern makes record IDs unstable.
3. A 7-stage SDLC pipeline spawns Claude Code 7+ times. Each spawn: process creation, prompt loading, context rebuild.
4. The Observer is a 4-phase LLM with tools (`read_session`, `update_session`, `enqueue_continuation`, `deliver_to_telegram`) to make mostly deterministic routing decisions.
5. A $5 hard budget cap kills productive sessions mid-work (#458).
6. Queue is per-project — two chat groups for the same project block each other.

**Desired outcome:**
```
Message → Job created → Queue (per chat_id) → Worker →
  Spawn one AgentSession → Session works full pipeline →
  Observer nudges between stages → Deliver to Telegram
```
- Job represents the human's request. Session represents a Claude Code invocation.
- Claude Code spawned once per unit of work, not per stage.
- Observer is a thin deterministic nudger, not an LLM orchestrator.
- Queues are per chat group.

## Prior Art

- **#211**: Dual AgentSession creation per message — identified the symptom of conflated models, fixed by deduplication rather than separation
- **#321 / PR #321**: Observer Agent replaced auto-continue with stage-aware steerer — introduced the current Observer architecture this plan simplifies
- **#356 / PR #356**: Rewrote /sdlc as single-stage router — established Observer-steered model where /sdlc invokes one sub-skill and returns
- **#371 / PR #373**: Passed stop_reason to Observer — wired budget_exceeded into Observer routing
- **#436**: Made is_sdlc a derived property — moved toward derived state, away from stored flags
- **#440 / PR #451**: Session watchdog and Observer reliability — introduced activity-based stall detection (the safety mechanism we keep)
- **#450 / PR #456**: SDLC job playlist — added sequential issue processing via playlist hook

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #321 | Replaced auto-continue with Observer Agent | Correct direction, but Observer became an LLM orchestrator instead of a thin nudger. Each stage still re-spawns Claude Code. |
| PR #356 | Made /sdlc a single-stage router | Solved the "skill does too much" problem but pushed orchestration complexity into Observer + re-enqueue loop |
| PR #373 | Wired stop_reason to Observer | Added more decision paths to an already complex Observer (budget, rate limit, etc.) |
| PR #378 | Fixed cross-repo, classification race, typed outcomes | Patched symptoms of the double-classification and model conflation rather than fixing the architecture |
| #457 | Fixed is_sdlc_job() → is_sdlc | Bug existed because Observer and AgentSession evolved independently with no clean interface |

**Root cause pattern:** Each fix addressed symptoms within the current architecture rather than questioning whether the architecture itself (multi-spawn, external orchestrator, conflated models) was sound.

## Data Flow

### Current (12+ hops)
1. **Telegram** → handler() in telegram_bridge.py
2. **Routing** → should_respond_async() classifies response need (Ollama)
3. **Classification** → classify_work_request() classifies intent (Ollama/Haiku)
4. **Session creation** → AgentSession created in Redis
5. **Queue** → enqueue_job() pushes to per-project Redis queue
6. **Worker** → _worker_loop() pops job
7. **Re-classification** → get_agent_response_sdk() re-classifies intent
8. **Agent spawn** → ValorAgent created, Claude Code process spawned
9. **Skill execution** → /sdlc assesses state, invokes one sub-skill
10. **Agent exit** → Claude Code process exits
11. **Observer** → 4-phase LLM decides steer vs deliver
12. **Re-enqueue** → _enqueue_continuation() creates new job, back to step 6
13. **Repeat** 7x for full pipeline
14. **Summarizer** → formats output
15. **Delivery** → Telegram message sent

### Target (6 hops)
1. **Telegram** → handler() receives message
2. **Job created** → Job model in Redis, pushed to per-chat_id queue
3. **Worker** → pops Job, spawns one AgentSession (Claude Code)
4. **Session works** → full pipeline executes in single process, Observer nudges between stages via hooks
5. **Session exits** → output validated, formatted
6. **Delivery** → Telegram message sent

## Architectural Impact

- **New model**: `Job` — separates human-request tracking from execution
- **Refactored model**: `AgentSession` — stripped to execution-only concerns
- **Interface changes**: Observer shifts from LLM-with-tools to deterministic hook; job_queue splits into job management and session management
- **Coupling reduction**: Job ↔ Session is a clean parent/child with FK. Observer is a thin layer, not an orchestrator with 4 tools.
- **Data ownership**: Job owns Telegram conversation state (steering messages, delivery). Session owns execution state (sdlc_stages, slug, artifacts).
- **Reversibility**: Medium — model split is the hardest to reverse. Migration must be incremental.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (scope alignment at each phase)
- Review rounds: 2+ (model migration, Observer rewrite, integration)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Job and Session models use Redis via Popoto |
| Tests pass on main | `pytest tests/unit/ -x -q` | Clean baseline before refactor |
| #458 merged first | `gh issue view 458 --json state -q .state` returns CLOSED | Budget removal simplifies Observer |

## Solution

### Key Elements

- **Job model**: New Popoto model representing a human's request. Owns the queue position, steering messages, and child sessions. Keyed by chat_id for per-group queuing.
- **AgentSession refactor**: Stripped to execution concerns only. Parent job tracked via FK. SDLC state tracked as `sdlc_stages`. Slug derives branch name, plan path, worktree path.
- **Single-session SDLC**: Claude Code spawned once with full pipeline instructions. Stages execute sequentially within one process. Sub-skills unchanged.
- **Observer simplification**: Deterministic nudger. Remaining stages? Steer. Blocked? Deliver. No LLM calls for routing.
- **Per-chat-group queue**: Each Telegram chat_id gets its own serial queue. Different groups run in parallel.

### Flow

**Message arrives** → Job created (per chat_id queue) → **Worker pops Job** → Spawn AgentSession → **Claude Code works full pipeline** → Observer nudges between stages → **Session completes** → Format + deliver to Telegram → **Job marked complete**

Human steers mid-pipeline: **Follow-up message** → Job receives steering message → Routed to active Session

### Technical Approach

#### Phase 1: Model Split
- Create `Job` model in `models/job.py`
- Refactor `AgentSession` to remove queue/conversation fields
- Add `parent_job_id` FK on AgentSession
- Migrate existing code to use new models

#### Phase 2: Queue Rekey
- Change queue key from `project_key` to `chat_id`
- Update worker loop to manage per-chat-group workers
- Move steering message handling to Job model

#### Phase 3: Single-Session Pipeline
- Rewrite SDLC prompt: full pipeline spec instead of "invoke /sdlc"
- Remove re-enqueue loop between stages
- Add progress hook (PostToolUse) that sends Telegram updates on stage transitions
- Remove auto-continue caps (no longer needed)

#### Phase 4: Observer Simplification
- Replace 4-phase LLM Observer with deterministic logic
- Keep: stop_reason handling, stall detection integration
- Remove: LLM fallback, tool definitions, coaching message generation
- Observer reads sdlc_stages from session, decides steer/deliver programmatically

#### Phase 5: Cleanup
- Remove double classification (classify once at Job creation)
- Remove budget system (#458)
- Remove delete-and-recreate pattern
- Update all consumers

### Job Model

```python
class Job(Model):
    job_id = AutoKeyField()
    chat_id = Field()              # Telegram chat → queue key
    message_id = Field()           # Telegram message that created this
    sender_name = Field()
    message_text = Field()
    project_key = Field()
    status = Field()               # pending → running → completed/failed
    created_at = Field()
    steering_messages = Field()    # JSON list of human follow-ups
    result_text = Field(null=True) # what was delivered to Telegram
    current_session_seq = Field(null=True)  # monotonic counter for active session
```

Jobs are **never deleted** — they transition to terminal states (`completed`/`failed`) and are garbage-collected by TTL or archival sweep. This prevents orphaned sessions.

### AgentSession Model

```python
class AgentSession(Model):
    session_id = AutoKeyField()
    parent_job_id = Field()        # logical FK → Job (not enforced by Redis)
    chat_id = Field()              # denormalized from Job for hot-path queries
    sequence = Field()             # monotonic within parent Job
    claude_session_uuid = Field(null=True)  # for resume
    status = Field()               # running → completed/failed
    sdlc_stages = Field(null=True) # JSON dict, null if not SDLC
    slug = Field(null=True)        # derives branch, plan path, worktree
    artifacts = Field(null=True)   # JSON: {issue_url, plan_url, pr_url}
    created_at = Field()
```

Derived properties:
- `is_sdlc` → `self.sdlc_stages is not None`
- `current_stage` → first stage with status `in_progress`
- `branch_name` → `f"session/{self.slug}"`
- `plan_path` → `f"docs/plans/{self.slug}.md"`

### Queue Implementation

Use a **Redis List** keyed as `job_queue:{chat_id}` for the queue (outside Popoto), with the Job model in Popoto for metadata. The list holds job IDs; dequeue is atomic `RPOP job_queue:{chat_id}`, then load `Job.get(job_id)`. This separates ordering (list) from data (hash) cleanly and gives atomic dequeue without application-level locking.

### Referential Integrity Strategy

Redis/Popoto has no FK enforcement. Integrity is maintained by convention:
- `chat_id` is denormalized onto AgentSession for hot-path observer queries (avoids two-step Job→Session lookup)
- `parent_job_id` is a logical FK; orphan detection runs in the watchdog sweep
- `current_session_seq` on Job + `sequence` on Session gives O(1) "active session" lookup
- Jobs are never deleted while sessions reference them (TTL-based cleanup only after terminal state)

### Session Creation Contract

Sessions are created exclusively by the **Job worker** (not by the Observer, not by the bridge handler):
1. Worker pops Job from queue
2. Worker creates AgentSession with `parent_job_id=job.job_id`, `sequence=job.current_session_seq + 1`
3. Worker updates `job.current_session_seq`
4. Worker spawns Claude Code with session context

Human messages arriving mid-session route as **steering messages on the Job**, not as new sessions. The active session reads steering messages from the Job during execution (via hook).

If a session crashes and the Job needs to retry, the worker creates a **new** AgentSession with incremented sequence number. The crashed session remains as a completed/failed record.

### Steering Message Safety

Steering messages use a **bounded Redis List** (`LPUSH` + `LTRIM` to cap at 10) keyed as `steering:{job_id}`. The active session reads via `RPOP` during PostToolUse hooks. This prevents unbounded accumulation during long-running sessions. If the buffer is full, the oldest unread steering message is dropped (human can re-send).

### Session Liveness

Long-running sessions must have a liveness mechanism:
- **Per-API-call timeout**: `asyncio.wait_for` on each Claude SDK call (existing `SDK_INACTIVITY_TIMEOUT_SECONDS`, default 300s)
- **Session max lifetime**: 60 minutes hard cap; if exceeded, session is killed and Job can retry
- **Heartbeat**: Activity-based stall detection (from #440) writes timestamps; watchdog checks for staleness

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Observer deterministic path: test all stop_reason values produce correct steer/deliver decision
- [ ] Job queue worker: test session spawn failure → Job marked failed, error delivered to Telegram
- [ ] Session resume: test crash mid-pipeline → new session spawned by Job, continues from last completed stage

### Empty/Invalid Input Handling
- [ ] Job with empty message_text → still created, session decides how to handle
- [ ] Session with null sdlc_stages → treated as non-SDLC, no stage nudging
- [ ] Observer receives empty session output → deliver with "(empty output)" fallback

### Error State Rendering
- [ ] Failed session → Job delivers error message to Telegram with context
- [ ] Stall detection fires → session killed, Job informed, delivers partial output

## Test Impact

Major refactor — nearly all test files touching these components need updates:

**DELETE (removed functionality):**
- [ ] `tests/unit/test_auto_continue.py` (22 tests) — DELETE: auto-continue loop eliminated
- [ ] `tests/unit/test_stop_reason_observer.py` (7 tests) — REPLACE: Observer no longer LLM-based
- [ ] `tests/unit/test_observer_early_return.py` (18 tests) — DELETE: no LLM Observer to early-return from
- [ ] `tests/unit/test_observer_message_for_user.py` (11 tests) — DELETE: Observer no longer generates messages

**REPLACE (new interfaces):**
- [ ] `tests/unit/test_observer.py` (36 tests) — REPLACE: rewrite for deterministic Observer
- [ ] `tests/unit/test_sdk_client_sdlc.py` (38 tests) — UPDATE: single-session model changes SDK invocation
- [ ] `tests/unit/test_sdlc_playlist.py` (11 tests) — UPDATE: playlist hooks attach to Job, not AgentSession
- [ ] `tests/unit/test_work_request_classifier.py` (16 tests) — UPDATE: classification happens once at Job creation
- [ ] `tests/unit/test_sdlc_env_vars.py` (10 tests) — UPDATE: env vars set once, not per-stage
- [ ] `tests/unit/test_sdlc_mode.py` (6 tests) — UPDATE: is_sdlc derived from sdlc_stages on session

**UPDATE (model changes):**
- [ ] `tests/unit/test_session_status.py` (15 tests) — UPDATE: status tracked on Job and Session separately
- [ ] `tests/unit/test_session_tags.py` (33 tests) — UPDATE: tags may move to Job model
- [ ] `tests/unit/test_model_relationships.py` (30 tests) — UPDATE: new Job → Session relationship
- [ ] `tests/unit/test_job_hierarchy.py` (22 tests) — UPDATE: hierarchy uses new Job model
- [ ] `tests/unit/test_pipeline_state_machine.py` (49 tests) — UPDATE: state machine reads from session.sdlc_stages
- [ ] `tests/unit/test_pipeline_integrity.py` (30 tests) — UPDATE: integrity checks use new models
- [ ] `tests/integration/test_agent_session_lifecycle.py` (58 tests) — REPLACE: lifecycle split across Job + Session
- [ ] `tests/integration/test_stage_aware_auto_continue.py` (39 tests) — REPLACE: stage progression is internal, not auto-continue
- [ ] `tests/integration/test_enqueue_continuation.py` (29 tests) — DELETE: no re-enqueue loop
- [ ] `tests/integration/test_steering.py` (32 tests) — UPDATE: steering goes through Job, not session directly
- [ ] `tests/integration/test_job_queue_race.py` (13 tests) — UPDATE: queue keyed by chat_id
- [ ] `tests/integration/test_job_scheduler.py` (21 tests) — UPDATE: scheduler uses Job model
- [ ] `tests/e2e/test_message_pipeline.py` (36 tests) — REPLACE: full pipeline flow changed
- [ ] `tests/e2e/test_session_continuity.py` (12 tests) — UPDATE: continuity via Job + session resume

**Estimated test impact: ~600 tests across 24 files need changes.**

## Rabbit Holes

- **Migrating existing Redis data** — Don't. Old AgentSession records can be left as-is or bulk-deleted. No migration of live data.
- **Making the Observer an LLM "sometimes"** — Deterministic only. If it can't decide, deliver to human. No "smart fallback."
- **Per-stage budget tracking** — Budget is being removed (#458). Don't add per-stage cost tracking.
- **Rewriting sub-skills** — /do-plan, /do-build, etc. are unchanged. Only the orchestration layer changes.
- **Multi-session parallelism** — A Job spawning parallel sessions (e.g., BUILD + TEST simultaneously) is a future concern. Keep it serial for now.

## Risks

### Risk 1: Long-running single session hits SDK/API limits
**Impact:** Full pipeline in one session could run 30+ minutes. Unknown SDK behavior at that duration.
**Mitigation:** Activity-based stall detection already handles this. Session continuation handles crashes. Test with a real full-pipeline run before shipping.

### Risk 2: Context window exhaustion in single session
**Impact:** A full SDLC pipeline generates a lot of tool output. Could exhaust Claude's context window mid-pipeline.
**Mitigation:** Claude Code handles context management internally (compression, summarization). Sub-agents for heavy tasks (PR review) keep the main context clean. Monitor in practice.

### Risk 3: Breaking the bridge during incremental migration
**Impact:** Bridge must stay operational throughout. A bad deploy could block all Telegram processing.
**Mitigation:** Phase the work: model split first (backward compatible), then queue rekey, then session changes, then Observer. Each phase is independently deployable. Keep old code paths until new ones are validated.

### Risk 4: Test suite disruption
**Impact:** ~600 tests need changes. Risk of test rot during migration.
**Mitigation:** Phase 1 (model split) updates tests first. Each subsequent phase updates its own tests before merging. Never merge with failing tests.

## Race Conditions

### Race 1: Steering message arrives while session is between stages
**Location:** Job.steering_messages, Observer nudge hook
**Trigger:** Human sends follow-up at the exact moment Observer is deciding steer/deliver
**Data prerequisite:** Job.steering_messages must be populated before Observer reads it
**State prerequisite:** Session must be in running state
**Mitigation:** Observer reads steering_messages atomically from Job before making decision. Redis operations are single-threaded.

### Race 2: Two messages from same chat group arrive near-simultaneously
**Location:** Job queue per chat_id
**Trigger:** User sends two messages in rapid succession
**Data prerequisite:** First Job must be enqueued before second is created
**State prerequisite:** Queue must serialize correctly
**Mitigation:** Per-chat_id queue with atomic `RPOP`. Second message creates a separate Job that waits in queue. Deduplication logic in handler prevents true duplicates.

### Race 3: TOCTOU on session lookup + steering injection
**Location:** Active session registry, steering message injection
**Trigger:** Human sends follow-up at exact moment session completes and is removed from registry
**Data prerequisite:** Session must be registered as active before steering check
**State prerequisite:** Session must still be running when steering message is injected
**Mitigation:** Per-chat_id `asyncio.Lock` guards the check-and-inject as atomic. Session transitions through `ACTIVE → DRAINING → DONE`; messages arriving during DRAINING are re-queued as new Jobs rather than steered.

### Race 4: Concurrent observer ticks dequeue same job
**Location:** Worker loop, job queue
**Trigger:** Previous worker tick slow, next fires before completion
**Data prerequisite:** Job must be in queue
**State prerequisite:** Only one worker should process a given job
**Mitigation:** Atomic `RPOP` on Redis List guarantees exactly-once dequeue. Job status transitions from `pending → running` immediately after pop, before any async work begins.

## No-Gos (Out of Scope)

- **Parallel session execution** — Jobs spawn sessions serially. Parallel sessions (e.g., running BUILD and DOCS simultaneously) is future work.
- **PM persona sessions** — All AgentSessions use Dev persona. PM persona is for channel responses and Observer voice only.
- **Rewriting sub-skills** — /do-plan, /do-build, /do-test, /do-pr-review, /do-docs, /do-merge are unchanged.
- **Telegram bot API migration** — Keep Telethon. Don't switch to Bot API.
- **Redis → PostgreSQL** — Keep Popoto/Redis. Don't change the storage layer.
- **Multi-project sessions** — One session targets one project. Cross-project orchestration is future work.

## Update System

- No new dependencies or services
- No new config files to propagate
- After deploy: restart bridge (`./scripts/valor-service.sh restart`)
- Old Redis AgentSession records are harmless — no data migration needed
- The update skill itself needs no changes

## Agent Integration

No agent integration required — this is a bridge-internal architectural refactor. The agent (Claude Code) receives messages and uses tools exactly as before. The change is in how sessions are spawned and orchestrated, not in what tools are available.

One change the agent will notice: SDLC sessions receive the full pipeline spec in the initial message instead of being told to "invoke /sdlc immediately." The /sdlc skill itself may be simplified or removed once the pipeline spec is in the prompt.

## Documentation

- [ ] Create `docs/features/job-session-architecture.md` describing the Job/Session split and lifecycle
- [ ] Update `docs/features/observer-agent.md` to reflect deterministic Observer
- [ ] Update `docs/features/pipeline-graph.md` if Observer integration changes
- [ ] Update `CLAUDE.md` system architecture diagram
- [ ] Archive or update `docs/features/sdlc-enforcement.md`
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] `Job` model exists and is used for all incoming messages
- [ ] `AgentSession` has exactly these fields: session_id, parent_job_id, claude_session_uuid, status, sdlc_stages, slug, artifacts, created_at
- [ ] Job queue is keyed by `chat_id`
- [ ] Full SDLC pipeline (issue → merge) completes in a single Claude Code session
- [ ] Observer makes zero LLM calls for routing decisions
- [ ] Classification happens once per Job
- [ ] Budget system fully removed (#458)
- [ ] All tests pass (unit, integration, e2e)
- [ ] Bridge processes messages correctly after deploy
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: model-builder
  - Role: Create Job model, refactor AgentSession, update FK relationships
  - Agent Type: builder
  - Resume: true

- **Builder (queue)**
  - Name: queue-builder
  - Role: Rekey queue to chat_id, update worker loop, move steering to Job
  - Agent Type: builder
  - Resume: true

- **Builder (session)**
  - Name: session-builder
  - Role: Implement single-session SDLC, rewrite prompt, add progress hook
  - Agent Type: builder
  - Resume: true

- **Builder (observer)**
  - Name: observer-builder
  - Role: Replace LLM Observer with deterministic logic
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Remove double classification, budget system, delete-and-recreate pattern
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify full pipeline e2e, model relationships, queue behavior
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: test-engineer
  - Role: Update ~600 affected tests across 24 files
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create/update feature docs, architecture diagrams
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Remove budget system (#458)
- **Task ID**: build-budget-removal
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_observer.py tests/unit/test_sdk_client.py -x -q`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Remove max_budget_usd from ValorAgent
- Remove SDK_MAX_BUDGET_USD and _COST_WARN_THRESHOLD
- Remove budget_exceeded from Observer
- Update affected tests

### 2. Create Job model
- **Task ID**: build-job-model
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_model_relationships.py -x -q` (create)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `models/job.py` with Job Popoto model
- Add fields: job_id, chat_id, message_id, sender_name, message_text, project_key, status, created_at, steering_messages, result_text
- Add helper methods: add_steering_message(), get_sessions(), mark_complete()

### 3. Refactor AgentSession model
- **Task ID**: build-session-refactor
- **Depends On**: build-job-model
- **Validates**: `pytest tests/unit/test_model_relationships.py tests/unit/test_session_status.py -x -q`
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- Strip AgentSession to: session_id, parent_job_id, claude_session_uuid, status, sdlc_stages, slug, artifacts, created_at
- Add derived properties: is_sdlc, current_stage, branch_name, plan_path
- Migrate fields that belong on Job (chat_id, message_text, sender_name, steering_messages) to Job model
- Update all imports and references

### 4. Rekey queue to chat_id
- **Task ID**: build-queue-rekey
- **Depends On**: build-session-refactor
- **Validates**: `pytest tests/integration/test_job_queue_race.py tests/integration/test_job_scheduler.py -x -q`
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Change queue key from project_key to chat_id in job_queue.py
- Update _push_job, _pop_job, _worker_loop to use Job model
- Move steering message handling to Job
- Remove delete-and-recreate pattern — Jobs are immutable, Sessions are append-only children

### 5. Implement single-session SDLC
- **Task ID**: build-single-session
- **Depends On**: build-queue-rekey
- **Validates**: `pytest tests/unit/test_sdlc_mode.py tests/unit/test_sdlc_env_vars.py -x -q`
- **Assigned To**: session-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite SDLC message: full pipeline spec instead of "invoke /sdlc immediately"
- Remove re-enqueue loop in _execute_job
- Add PostToolUse progress hook that detects stage transitions and sends Telegram updates
- Remove auto-continue caps (MAX_AUTO_CONTINUES, MAX_AUTO_CONTINUES_SDLC)
- Remove _enqueue_continuation for SDLC stage progression

### 6. Simplify Observer
- **Task ID**: build-observer
- **Depends On**: build-single-session
- **Validates**: `pytest tests/unit/test_observer.py -x -q` (rewritten)
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace Observer class with deterministic function
- Logic: check session.sdlc_stages → remaining stages? steer. blocked/complete? deliver.
- Remove: _run_llm_observer(), _build_tools(), _build_observer_system_prompt()
- Keep: stop_reason handling, stall detection integration
- Remove double classification in sdk_client.py

### 7. Update test suite
- **Task ID**: build-tests
- **Depends On**: build-observer
- **Validates**: `pytest tests/ -x -q`
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete tests for removed functionality (auto-continue, LLM Observer, enqueue_continuation)
- Rewrite tests for new Job/Session models
- Rewrite e2e pipeline tests for single-session flow
- Ensure all 24 affected test files are updated

### 8. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-tests
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify Job → Session FK relationship works
- Verify per-chat_id queue serialization
- Verify full SDLC pipeline completes in single session
- Verify steering messages route through Job to active Session
- Verify Observer makes zero LLM calls

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/job-session-architecture.md`
- Update `docs/features/observer-agent.md`
- Update `CLAUDE.md` architecture diagram
- Add entry to `docs/features/README.md`

### 10. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Verify bridge starts and processes a test message

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Job model exists | `python -c "from models.job import Job; print('ok')"` | output contains ok |
| Session has slug | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'slug')"` | exit code 0 |
| No budget refs | `grep -rn 'max_budget_usd\|budget_exceeded\|COST_WARN' agent/ bridge/ --include='*.py'` | exit code 1 |
| No double classify | `grep -cn 'classify_work_request' agent/sdk_client.py` | output contains 0 |
| Observer no LLM | `grep -cn 'ClaudeAPIClient\|claude_client\|anthropic' bridge/observer.py` | output contains 0 |
| Queue uses chat_id | `grep -n 'chat_id' agent/job_queue.py \| head -1` | output contains chat_id |

## Migration Strategy

### In-Flight Session Handling
On deploy, restart the bridge. Any in-flight sessions are abandoned (existing crash recovery handles this). Old AgentSession records in Redis are harmless — they use different key patterns and won't collide with new Job/Session records. No data migration needed.

### Queue Cutover
Old queue keys (`queue:{project_key}`) will be empty after restart since workers drain on shutdown. New queue keys (`job_queue:{chat_id}`) start fresh. No messages lost because the bridge only enqueues after restart.

### Rollback Path
If bugs surface after Phase 1 (model split): revert the commit, restart bridge. Old AgentSession code paths still work because the model file is restored. New Job records in Redis are orphaned but harmless (TTL cleanup). Each phase is independently revertable via git revert + restart.

## RFC Feedback

| Severity | Critic | Feedback | Plan Response |
|----------|--------|----------|---------------|
| CONCERN | code-reviewer | Deterministic observer loses ability to handle novel failure modes | Added: if deterministic logic can't decide, deliver to human. No LLM fallback — ambiguity = escalate. |
| CONCERN | code-reviewer | Single-session serializes stages, roughly doubling wall-clock time | Acknowledged tradeoff. Offset by eliminating 7x spawn overhead. Sub-agents parallelize heavy subtasks. |
| CONCERN | code-reviewer | Phase 1 (model split) ships before Observer rewrite, so new models exercised only by compat shim | Accepted risk. Model split is low-risk (additive). Observer rewrite in Phase 4 validates the models. |
| CONCERN | async-specialist | Single asyncio.Lock contention on active_sessions dict | Addressed: use per-chat_id locks via `defaultdict(asyncio.Lock)` with brief global lock for insertion only. |
| CONCERN | async-specialist | Observer re-invocation as steering vs new queue entry underspecified | Addressed in Session Creation Contract: Observer steers via bounded Redis List, never spawns new sessions. |
| CONCERN | async-specialist | Popoto ORM uses synchronous Redis calls blocking event loop | Noted for future: migrate to redis.asyncio. For now, existing pattern works and sessions are I/O-bound on Claude API, not Redis. |
| CONCERN | async-specialist | Graceful shutdown of long-running sessions on bridge restart | Addressed in Session Liveness: 60-min max lifetime. On restart, sessions are abandoned and can resume via claude_session_uuid. |
| CONCERN | async-specialist | Memory growth from long Claude sessions | Claude Code handles context compression internally. Sub-agents for heavy tasks keep main context clean. Monitor in practice. |
| CONCERN | data-architect | job_type must be authoritative on Job, never re-derived from session | Addressed: removed classification from plan entirely. SDLC is a session property (sdlc_stages != null), not a Job property. |
| CONCERN | data-architect | No session sequence numbering for "current session" lookup | Addressed: added `sequence` field on AgentSession and `current_session_seq` on Job. |
| CONCERN | data-architect | No concurrent-dequeue protection specified | Addressed: atomic `RPOP` on Redis List guarantees exactly-once dequeue. |

## Open Questions

1. **Should the /sdlc skill be removed entirely?** If the full pipeline spec is in the prompt, /sdlc becomes redundant. But keeping it as a skill means it's versionable and editable without changing Python code. Recommend: keep /sdlc but rewrite it as the full pipeline spec (not a single-stage router).

2. **What happens to the playlist feature (#450)?** The playlist hook currently auto-pops the next issue after a job completes. In the new model, this hooks into Job completion rather than AgentSession completion. Should be a straightforward migration but needs explicit handling.

3. **Should the summarizer change?** Currently the summarizer formats SDLC output with stage progress lines. With single-session, the session output already contains all stages. The summarizer may need to extract a final summary rather than format per-stage output. Recommend: keep summarizer for now, adapt formatting to single-session output.

4. **Non-SDLC message in SDLC chat**: If "what time is it?" arrives in a chat with an active SDLC Job, should it create a new Job or be treated as steering? Recommend: new Job. The per-chat_id queue serializes it — it waits until the SDLC Job completes, then runs as a simple question.

5. **Slug deduplication**: If a new message arrives for an existing slug that has a pending Job, should it merge into the existing Job or create a new one? Recommend: new Job. Deduplication is fragile; let the queue serialize and the human can steer if needed.
