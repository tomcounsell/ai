---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-22
tracking: https://github.com/tomcounsell/ai/issues/465
last_comment_id:
---

# SDLC Redesign Phase 2: Complete Bridge Migration to ChatSession/DevSession Flow

## Problem

The bridge handler stamps messages with `session_type` but then feeds them into the legacy pipeline: `enqueue_job()` → `async_create()` → `_execute_job()` → Observer → `_enqueue_continuation()` → re-spawn Claude Code per stage. The Observer module (`bridge/observer.py`) contains SDLC stage awareness and routing logic that duplicates intelligence the ChatSession should own.

**Current behavior:**
1. Classification happens twice: once in `routing.py` (lightweight), then the result is read from the session in `sdk_client.py`
2. Every SDLC stage re-spawns Claude Code via `_enqueue_continuation()` — 7+ spawns per pipeline
3. The Observer decides steer vs deliver using SDLC-aware decision tables
4. Queue is keyed by `project_key` — two chat groups for the same project block each other
5. Bridge handler creates sessions via raw `async_create()` instead of factory methods

**Desired outcome:**
Three clean layers with zero overlap:
```
Bridge (Python) — Dumb barriers only. One nudge: "Keep working — only stop
                  when you need human input or you're done."
    ↕
ChatSession (Claude Code, PM persona) — Owns ALL intelligence: classifies work,
                  reads code, chooses slug, spawns/steers DevSessions, composes
                  delivery messages. Thinks like a PM, not a pipeline executor.
    ↕
DevSession (Claude Code subprocess) — Executes work. Full permissions, Dev persona.
```

## Prior Art

- **#459 / PR #464**: SDLC Redesign Phase 1 — built the foundation (model with session_type discriminator, deterministic Observer, DevSession registration hooks, simple session fast-path, dev-session agent definition). The current PR to complete.
- **#458**: Remove budget system — prerequisite, merged
- **#450 / PR #456**: SDLC Job Playlist — introduced playlist, then removed in Phase 1
- **#321 / PR #321**: Observer Agent — introduced the LLM Observer that this plan deletes
- **#356 / PR #356**: Made /sdlc a single-stage router — established the Observer-steered model
- **#440 / PR #451**: Session watchdog — introduced activity-based stall detection (kept)

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #321 | Introduced LLM Observer to replace auto-continue | Added intelligence at the wrong layer — the bridge shouldn't understand SDLC |
| PR #356 | Made /sdlc single-stage, Observer orchestrates | Correct decomposition of /sdlc but pushed complexity into Observer + re-enqueue |
| PR #464 | Deterministic Observer, session_type model | Built the right model but left the old runtime path in place — half-migrated |

**Root cause pattern:** Intelligence was added to the bridge/Observer layer when it belongs in the ChatSession (the Claude Code process that can actually reason about SDLC stages, code state, and PM decisions).

## Data Flow

### Current (legacy path still active)
```
1. Telegram message arrives
2. Bridge handler → routing.py classifies (Ollama/Haiku)
3. Bridge → enqueue_job() → async_create() → Redis queue (keyed by project_key)
4. Worker loop → _pop_job() → _execute_job()
5. _execute_job() → get_agent_response_sdk() → ValorAgent.query()
6. Agent output → send_to_chat()
7. send_to_chat() → narration gate → Observer.run() → decision table
8. If steer → _enqueue_continuation() → new job → back to step 4
9. If deliver → send_cb() → Telegram
10. Repeat steps 4-9 for each SDLC stage (7+ times)
```

### Target (new path)
```
1. Telegram message arrives
2. Bridge handler → is this a reply to running session? (steering check)
3. If new message → create_chat() or create_simple() → Redis queue (keyed by chat_id)
4. Worker loop → pop session → start Agent SDK
5a. Simple session: Agent runs, output delivered directly to Telegram
5b. ChatSession (PM persona, read-only):
    - Reads message, decides what to do (classify, research, spawn DevSession)
    - Spawns DevSession via Agent tool if coding work needed
    - DevSession runs full pipeline in single subprocess
    - ChatSession composes delivery message
6. If Agent SDK stops mid-work → bridge nudge: "Keep working"
7. If ChatSession signals done → deliver to Telegram
```

## Architectural Impact

- **Deleted**: `bridge/observer.py` (entire module), `_enqueue_continuation()`, `classify_work_request` from SDLC path, `PipelineStateMachine` usage in bridge
- **Moved**: All SDLC intelligence → ChatSession prompt/persona. Pipeline state tracking → internal to ChatSession.
- **Simplified**: `send_to_chat()` becomes a single nudge loop. Bridge handler uses factory methods.
- **Changed**: Queue key from `project_key` to `chat_id`. Worker loop manages per-chat workers.
- **Kept**: Steering check (reply-to detection), intake classifier (interjection routing), media enrichment, session liveness/watchdog, DevSession registration hooks, `dev-session` agent definition
- **Coupling reduction**: Bridge no longer imports `PipelineStateMachine`, `Observer`, or any SDLC stage constants
- **Reversibility**: Medium — Observer deletion is hard to reverse but the old code is in git history

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (validate nudge model after Phase 1 observation, scope alignment)
- Review rounds: 2+ (bridge rewrite, integration testing on live bridge)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Phase 1 merged | `git log main --oneline -5 \| grep -q "SDLC Redesign"` | Foundation must be on main |
| Redis running | `redis-cli ping` | Session models use Redis |
| Bridge running | `./scripts/valor-service.sh status` | Must observe live behavior before building |
| Phase 1 observed | Manual: run 3+ SDLC jobs through live bridge, document behavior | Design informed by observation |

## Solution

### Key Elements

- **Single nudge model**: Bridge has ONE response to any non-completion: "Keep working — only stop when you need human input or you're done." No branching, no SDLC awareness.
- **ChatSession as PM**: Not a pipeline executor. A PM that reads code, classifies work, asks clarifying questions, runs spikes, spawns DevSessions, and composes delivery messages.
- **Completion detection**: Bridge determines "done vs still working" via either SDK's natural `stop_reason` behavior or a cheap Haiku classifier ("is this a final delivery?"). No magic tokens.
- **Per-chat queuing**: Queue keyed by `chat_id` so different chat groups run in parallel. Same-project parallel work uses worktrees.

### Flow

```
Message arrives
  → Bridge: steering check (reply to running session?)
    → If steering: push to session's steering queue, return
    → If new: classify as chat or simple via lightweight check

  → Simple session:
    create_simple() → single Agent SDK session → deliver to Telegram

  → ChatSession:
    create_chat() → queue per chat_id → worker pops → Agent SDK (PM persona, read-only)
    → ChatSession reads message, reads code, decides approach
    → May ask clarifying question → deliver to Telegram, wait for reply
    → May run spike research → continue working
    → Spawns DevSession via Agent tool when coding work needed
    → DevSession runs full pipeline (plan→build→test→review→docs→merge)
    → ChatSession composes delivery → deliver to Telegram

  → Bridge nudge loop (for any SDK stop that isn't completion):
    "Keep working — only stop when you need human input or you're done."
```

### Technical Approach

#### Phase 1: Observe Live Behavior (before building)
- Deploy Phase 1 (PR #464, now merged) to the live bridge
- Run 3+ SDLC jobs and 5+ simple Q&A messages
- Document: How often does ChatSession stop unexpectedly? What does the single-nudge look like in practice? Where does context get lost?
- This informs the exact nudge wording and completion detection approach

#### Phase 2: Replace send_to_chat with Nudge Loop
- Gut `send_to_chat()` in `_execute_job`: remove Observer import, narration gate, pipeline state machine, _enqueue_continuation
- Replace with: check if output signals completion → deliver; otherwise → nudge with "keep working"
- Completion detection: use Haiku classifier or just check if stop_reason == "end_turn" and output length > threshold
- Keep rate_limit handling (wait + nudge)

#### Phase 3: Bridge Handler → Factory Methods
- Replace `enqueue_job()` call with `AgentSession.create_chat()` or `AgentSession.create_simple()`
- Remove `classify_work_request` from the SDLC path — ChatSession classifies
- Keep steering check and intake classifier (these route replies, not classify work type)
- Simple messages bypass ChatSession entirely

#### Phase 4: Rekey Queue to chat_id
- Change queue key from `project_key` to `chat_id`
- Update `_ensure_worker`, `_worker_loop`, `_pop_job`, `_push_job`
- Add old-key drain on startup (migration period)
- Verify worktree manager handles same-project parallel work from different chats

#### Phase 5: Delete Observer and Legacy Code
- Delete `bridge/observer.py`
- Delete `_enqueue_continuation()`
- Remove `PipelineStateMachine` usage from bridge (ChatSession uses it internally via /sdlc skill)
- Remove all Observer imports from job_queue.py, conftest.py, etc.
- Remove `clear_observer_state`, telemetry Observer functions

#### Phase 6: Update Tests and Validate
- Delete/rewrite tests for removed components
- Write new tests for nudge loop behavior
- Run full SDLC pipeline on live bridge end-to-end
- Validate: exactly 2 Claude Code spawns per SDLC job (ChatSession + DevSession)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Nudge loop: test that rate_limited stop_reason triggers backoff before nudge
- [ ] Nudge loop: test that max nudge count (safety cap) delivers to Telegram instead of infinite loop
- [ ] Factory method failure: test that create_chat() error falls back gracefully
- [ ] DevSession registration failure: test that hook failure doesn't block ChatSession

### Empty/Invalid Input Handling
- [ ] Empty ChatSession output → nudge (not deliver empty message)
- [ ] ChatSession with empty message_text → still created, PM decides what to do
- [ ] Nudge loop with no output at all after N nudges → deliver error to Telegram

### Error State Rendering
- [ ] Failed DevSession → ChatSession detects and delivers error context
- [ ] Stall detection fires → session killed, error delivered to Telegram

## Test Impact

Major refactor — tests for deleted components need removal, tests for bridge behavior need rewriting.

**DELETE (removed components):**
- [ ] `tests/unit/test_observer.py` (21 tests) — DELETE: Observer module deleted [Phase 5]
- [ ] `tests/unit/test_message_quality.py` (30 tests) — UPDATE: narration gate moves to nudge loop [Phase 2]
- [ ] `tests/unit/test_work_request_classifier.py` (16 tests) — UPDATE: classify_work_request removed from SDLC path [Phase 3]

**REPLACE (new interfaces):**
- [ ] `tests/unit/test_sdk_client_sdlc.py` (38 tests) — UPDATE: SDLC prompt changes, no classification [Phase 3]
- [ ] `tests/integration/test_agent_session_lifecycle.py` (58 tests) — REPLACE: lifecycle now ChatSession→DevSession [Phase 6]
- [ ] `tests/integration/test_job_queue_race.py` (13 tests) — UPDATE: queue keyed by chat_id [Phase 4]
- [ ] `tests/integration/test_job_scheduler.py` (21 tests) — UPDATE: scheduler uses chat_id [Phase 4]

**UPDATE (model/interface changes):**
- [ ] `tests/unit/test_cross_wire_fixes.py` — UPDATE: remove Observer imports [Phase 5]
- [ ] `tests/unit/test_duplicate_delivery.py` — UPDATE: remove Observer imports [Phase 5]
- [ ] `tests/unit/test_pipeline_integrity.py` — UPDATE: pipeline state used by ChatSession, not bridge [Phase 5]
- [ ] `tests/unit/test_sdlc_env_vars.py` — UPDATE: env vars set by ChatSession, not sdk_client [Phase 3]
- [ ] `tests/unit/test_telemetry.py` — UPDATE: remove Observer telemetry [Phase 5]
- [ ] `tests/unit/test_stall_detection.py` — UPDATE: queue key change [Phase 4]
- [ ] `tests/unit/test_pending_recovery.py` — UPDATE: queue key change [Phase 4]
- [ ] `tests/integration/test_silent_failures.py` — UPDATE: remove _enqueue_continuation refs [Phase 5]
- [ ] `tests/integration/test_connectivity_gaps.py` — UPDATE: queue key change [Phase 4]
- [ ] `tests/integration/test_lifecycle_transition.py` — UPDATE: queue key change [Phase 4]
- [ ] `tests/integration/test_job_health_monitor.py` — UPDATE: queue key change [Phase 4]
- [ ] `tests/e2e/test_message_pipeline.py` — REPLACE: full pipeline flow changed [Phase 6]
- [ ] `tests/conftest.py` — UPDATE: remove Observer mock setup [Phase 5]

**NEW tests to write:**
- [ ] `tests/unit/test_nudge_loop.py` — Test nudge behavior: completion detection, rate-limit backoff, max nudge safety cap
- [ ] `tests/unit/test_chat_session_factory.py` — Test factory method integration in bridge handler
- [ ] `tests/integration/test_chat_dev_session_flow.py` — Test ChatSession spawns DevSession, DevSession completes, delivery

**Estimated test impact: ~300+ tests across 20+ files need changes. Each phase updates its own tests.**

## Rabbit Holes

- **Designing the perfect nudge message** — Ship with a simple "keep working" and iterate based on observation. Don't A/B test nudge wording.
- **Building a PM personality system** — ChatSession's PM behavior emerges from the persona prompt. Don't build a separate PM framework.
- **Parallel DevSessions** — ChatSession spawning multiple DevSessions simultaneously is future work. Keep it serial.
- **Migrating PipelineStateMachine** — ChatSession uses /sdlc skill internally, which uses PipelineStateMachine. Don't rewrite PipelineStateMachine — just stop the bridge from importing it.
- **Optimizing the Haiku completion classifier** — Start with a simple heuristic (output length + stop_reason). Only add Haiku classifier if heuristic fails in practice.

## Risks

### Risk 1: ChatSession stops too frequently (SDK limitation)
**Impact:** If Agent SDK stops ChatSession after every tool call or message, the nudge loop fires constantly, adding latency.
**Mitigation:** Phase 1 observation will quantify this. If excessive, explore SDK configuration (longer timeouts, batch tool calls) or accept the latency as a tradeoff for architectural cleanliness.

### Risk 2: Context window exhaustion in single ChatSession
**Impact:** ChatSession orchestrating a full SDLC pipeline accumulates a lot of context. May lose early context (the original message, plan requirements).
**Mitigation:** ChatSession uses the Agent tool to spawn DevSession as a subprocess — this isolates DevSession's heavy tool output from ChatSession's context. ChatSession stays light (orchestration only). If still an issue, ChatSession can write summaries to disk between stages.

### Risk 3: Breaking the bridge during migration
**Impact:** Bridge must stay operational throughout. A bad deploy blocks all Telegram processing.
**Mitigation:** Phase incrementally. Each phase is independently deployable and revertable. Keep old code paths behind a feature flag during transition if needed (e.g., `USE_NUDGE_LOOP=true`).

### Risk 4: Completion detection false positives/negatives
**Impact:** Bridge delivers mid-work output to Telegram (false positive) or infinite nudge loop (false negative).
**Mitigation:** Start with conservative heuristic (only deliver when stop_reason == "end_turn" AND output is substantial). Add safety cap on nudge count (e.g., 50). Log every nudge for debugging.

## Race Conditions

### Race 1: Steering message arrives during nudge
**Location:** Bridge nudge loop, steering queue
**Trigger:** Human replies while bridge is deciding whether to nudge or deliver
**Data prerequisite:** Steering message must be in queue before bridge reads it
**State prerequisite:** ChatSession must still be running
**Mitigation:** Read steering queue before deciding to nudge. If steering exists, pass it to ChatSession instead of generic nudge.

### Race 2: Two messages from same chat arrive simultaneously
**Location:** Chat queue per chat_id
**Trigger:** User sends two messages in rapid succession
**Data prerequisite:** First session must be queued before second
**State prerequisite:** Queue must serialize correctly
**Mitigation:** Atomic `RPOP` on Redis List. Second message creates separate ChatSession that waits in queue.

### Race 3: Bridge restart during active ChatSession
**Location:** Queue cutover during restart
**Trigger:** Messages arrive during ~2s restart window
**Data prerequisite:** Telethon must reconnect and fetch missed messages
**Mitigation:** Telethon persistent sessions with `pts` tracking fetch messages on reconnect. New code drains old queue keys on startup.

## No-Gos (Out of Scope)

- **Parallel DevSessions** — ChatSession spawns one DevSession at a time. Parallel is future work.
- **PM personality framework** — ChatSession's behavior comes from the persona prompt, not a separate system.
- **Redis → PostgreSQL** — Keep Popoto/Redis. Don't change the storage layer.
- **Telegram Bot API migration** — Keep Telethon.
- **Rewriting sub-skills** — /do-plan, /do-build, etc. are unchanged. Only the orchestration layer changes.
- **Full async Popoto migration** — Hot-path calls already wrapped in asyncio.to_thread() from Phase 1.

## Update System

- No new dependencies or services
- After deploy: restart bridge (`./scripts/valor-service.sh restart`)
- Old Redis session records are harmless — no data migration needed
- Feature flag `USE_NUDGE_LOOP` may be added during transition for safe rollout

## Agent Integration

No agent integration required — this is a bridge-internal architectural refactor. The agent (Claude Code) receives messages and uses tools exactly as before. The change is in how sessions are spawned and orchestrated, not in what tools are available.

ChatSession's PM persona is configured via `agent/sdk_client.py` (system prompt, permission mode) and the persona overlay files. No new MCP tools needed.

## Documentation

- [ ] Update `docs/features/chat-dev-session-architecture.md` — add nudge loop behavior, remove Observer references
- [ ] Delete or archive `docs/features/observer-agent.md` — Observer no longer exists
- [ ] Update `docs/features/bridge-workflow-gaps.md` — remove auto-continue cap references, document nudge model
- [ ] Update `CLAUDE.md` — remove Observer references from architecture section
- [ ] Update `docs/features/README.md` — remove Observer entry or mark as historical
- [ ] Add entry for nudge loop / bridge barriers if substantial enough for its own doc

## Success Criteria

- [ ] `bridge/observer.py` does not exist
- [ ] `_enqueue_continuation` function does not exist in `job_queue.py`
- [ ] `send_to_chat()` contains only nudge loop logic — no SDLC stage names, no PipelineStateMachine
- [ ] Bridge handler calls `create_chat()` / `create_simple()` factory methods
- [ ] No `classify_work_request` in the SDLC message path
- [ ] Queue key is `chat_id`, not `project_key`
- [ ] Full SDLC pipeline completes with exactly 2 Claude Code spawns (ChatSession + DevSession)
- [ ] Simple Q&A messages work with single session, direct delivery
- [ ] All tests pass
- [ ] Bridge processes messages correctly on live deployment
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (nudge-loop)**
  - Name: nudge-builder
  - Role: Replace send_to_chat with nudge loop, remove Observer from _execute_job
  - Agent Type: builder
  - Resume: true

- **Builder (factory-methods)**
  - Name: factory-builder
  - Role: Bridge handler uses create_chat/create_simple, remove classify_work_request from SDLC path
  - Agent Type: builder
  - Resume: true

- **Builder (queue-rekey)**
  - Name: queue-builder
  - Role: Rekey queue from project_key to chat_id, update worker loop
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Delete observer.py, _enqueue_continuation, all legacy imports
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify nudge loop, factory methods, queue behavior, end-to-end pipeline
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: test-engineer
  - Role: Update ~300 affected tests, write new nudge loop tests
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update/delete feature docs, CLAUDE.md, README
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Observe Phase 1 on Live Bridge
- **Task ID**: observe-live
- **Depends On**: none
- **Validates**: Manual observation notes documented
- **Assigned To**: human (Tom)
- **Parallel**: true
- Deploy Phase 1 to live bridge
- Run 3+ SDLC jobs, 5+ Q&A messages
- Document: SDK stop frequency, nudge behavior, context loss points
- Decide: nudge wording, completion detection approach

### 2. Replace send_to_chat with Nudge Loop
- **Task ID**: build-nudge-loop
- **Depends On**: observe-live
- **Validates**: `pytest tests/unit/test_nudge_loop.py -x -q` (create)
- **Assigned To**: nudge-builder
- **Agent Type**: builder
- **Parallel**: false
- Gut send_to_chat(): remove Observer, narration gate, PipelineStateMachine, _enqueue_continuation
- Replace with: completion check → deliver or nudge
- Add max nudge safety cap (50)
- Keep rate_limit handling (wait + nudge)
- **Update tests**: test_message_quality.py, test_observer.py references in conftest

### 3. Bridge Handler → Factory Methods
- **Task ID**: build-factory-methods
- **Depends On**: build-nudge-loop
- **Validates**: `pytest tests/unit/test_chat_session_factory.py -x -q` (create)
- **Assigned To**: factory-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace enqueue_job() with create_chat()/create_simple()
- Remove classify_work_request from SDLC path
- Keep steering check and intake classifier
- **Update tests**: test_work_request_classifier.py, test_sdk_client_sdlc.py

### 4. Rekey Queue to chat_id
- **Task ID**: build-queue-rekey
- **Depends On**: build-factory-methods
- **Validates**: `pytest tests/integration/test_job_queue_race.py tests/integration/test_job_scheduler.py -x -q`
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Change queue key from project_key to chat_id
- Update _ensure_worker, _worker_loop, _pop_job, _push_job
- Add old-key drain on startup
- Verify worktree manager handles parallel same-project work
- **Update tests**: test_job_queue_race.py, test_job_scheduler.py, test_stall_detection.py, test_pending_recovery.py, test_connectivity_gaps.py, test_lifecycle_transition.py, test_job_health_monitor.py

### 5. Delete Observer and Legacy Code
- **Task ID**: build-cleanup
- **Depends On**: build-queue-rekey
- **Validates**: `test ! -f bridge/observer.py && grep -c '_enqueue_continuation' agent/job_queue.py | grep -q '^0$'`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete bridge/observer.py
- Delete _enqueue_continuation from job_queue.py
- Remove all Observer imports (conftest.py, job_queue.py, etc.)
- Remove PipelineStateMachine usage from bridge
- Remove Observer telemetry functions
- **Update tests**: test_cross_wire_fixes.py, test_duplicate_delivery.py, test_pipeline_integrity.py, test_telemetry.py, test_silent_failures.py, conftest.py

### 6. Update Test Suite
- **Task ID**: build-tests
- **Depends On**: build-cleanup
- **Validates**: `pytest tests/ -x -q`
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Write test_nudge_loop.py, test_chat_session_factory.py, test_chat_dev_session_flow.py
- Update/replace test_agent_session_lifecycle.py, test_message_pipeline.py
- Final sweep for any remaining broken imports

### 7. Validate Integration
- **Task ID**: validate-integration
- **Depends On**: build-tests
- **Validates**: `pytest tests/ -x -q && python -m ruff check .`
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify nudge loop delivers correctly
- Verify factory methods create correct session types
- Verify per-chat queue serialization
- Verify no Observer module or imports remain
- Verify bridge has zero SDLC stage awareness

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update chat-dev-session-architecture.md
- Delete/archive observer-agent.md
- Update CLAUDE.md architecture section
- Update bridge-workflow-gaps.md

### 9. End-to-End Validation on Live Bridge
- **Task ID**: validate-live
- **Depends On**: document-feature
- **Assigned To**: human (Tom) + integration-validator
- **Parallel**: false
- Deploy to live bridge
- Run full SDLC pipeline (issue → merge)
- Verify exactly 2 Claude Code spawns
- Verify delivery messages are correct
- Verify simple Q&A still works

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| No Observer module | `test ! -f bridge/observer.py` | exit code 0 |
| No Observer imports | `grep -rn 'from bridge.observer\|import.*Observer' agent/ bridge/ --include='*.py'` | exit code 1 |
| No _enqueue_continuation | `grep -c '_enqueue_continuation' agent/job_queue.py` | 0 |
| No SDLC stages in bridge | `grep -n 'ISSUE\|PLAN\|BUILD\|TEST\|PATCH\|REVIEW\|DOCS\|MERGE' agent/job_queue.py \| grep -v '#\|"""' \| wc -l` | 0 |
| Factory methods used | `grep -c 'create_chat\|create_simple' bridge/telegram_bridge.py` | 2+ |
| Queue uses chat_id | `grep -q 'chat_id' agent/job_queue.py && ! grep -q '_ensure_worker(project_key)' agent/job_queue.py` | exit code 0 |

## Open Questions

1. **Nudge wording**: After observing Phase 1 on the live bridge, what's the exact nudge message? Current proposal: "Keep working — only stop when you need human input or you're done." Should it be more specific?
2. **Completion detection**: Haiku classifier vs output-length heuristic vs SDK stop_reason alone? Observation will inform this.
3. **Feature flag**: Should we use `USE_NUDGE_LOOP=true` for gradual rollout, or rip and replace in one deploy?
4. **Worktree parallel safety**: When two chat groups target the same project with chat_id queuing, does the worktree manager handle creating separate worktrees automatically? Or does ChatSession need to explicitly request a worktree?
