---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-21
tracking: https://github.com/tomcounsell/ai/issues/459
last_comment_id:
---

# SDLC Redesign: PM/Dev session Split, Single-Session Pipeline, Observer Simplification

> **Note**: References to `sdlc_stages` in this plan are outdated. As of [#488](https://github.com/tomcounsell/ai/pull/490), the `sdlc_stages` field was consolidated into `stage_states`. Any unimplemented items in this plan that reference `sdlc_stages` should use `stage_states` instead.

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
Message → PM session created (read-only, PM persona) → Queue (per chat_id) →
  PM session reads code, chooses slug, decides what to do →
  Spawns Dev session (full permissions, Dev persona) →
  Dev session works full pipeline → PM session nudges between stages →
  PM session composes delivery → Telegram
```
- Both are Agent SDK sessions sharing an `AgentSession` base class.
- `PM session` (read-only, PM persona) owns the Telegram conversation, orchestrates.
- `Dev session` (full permissions, Dev persona) does the actual work.
- Claude Code spawned once per unit of work, not per stage.
- Queues are per chat group.

## Spike Results

### spike-1: Can Agent SDK create read-only sessions?
- **Assumption**: "We can spawn PM session as a read-only Claude Code process"
- **Method**: code-read
- **Finding**: YES. SDK supports `permission_mode="plan"`, `allowed_tools=[...]`, and `disallowed_tools=[...]`. The validator agent already uses `_READ_ONLY_TOOLS` list. Multiple orthogonal restriction mechanisms available.
- **Confidence**: high
- **Impact on plan**: PM session can use `permission_mode="plan"` with `disallowed_tools=["Write", "Edit", "NotebookEdit"]`

### spike-2: Does Popoto ORM support model inheritance?
- **Assumption**: "We can do `class PM session(AgentSession)` with Popoto"
- **Method**: code-read + prototype
- **Finding**: NO. Popoto's metaclass (`ModelBase`) does not inherit parent fields into child `_meta.field_names`. After save+reload, parent fields are silently lost. The `# todo: handle multiple inheritance` comment at line 366 confirms this is a known limitation.
- **Confidence**: high
- **Impact on plan**: BLOCKER resolved — switched to single model with `session_type` discriminator field instead of class inheritance. Factory methods provide type safety at the Python level.

### spike-3: How does PM session spawn Dev session?
- **Assumption**: "A read-only Claude Code session can programmatically spawn a full-permission session"
- **Method**: code-read
- **Finding**: YES. Claude Code's built-in Agent tool can invoke agents defined in `agent_definitions.py`. A new `dev-session` agent with `tools=None` (all tools) gives full write permissions. The validator/code-reviewer agents already demonstrate the pattern of tool-restricted agents. PM session invokes `@Agent dev-session` which spawns a subprocess with full access.
- **Confidence**: high
- **Impact on plan**: Dev session spawning works via existing Agent tool infrastructure. No new bridge tools or MCP servers needed.

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

### Target (7 hops)
1. **Telegram** → handler() receives message
2. **PM session created** → queued per chat_id
3. **Worker** → pops PM session, starts Agent SDK (read-only, PM persona)
4. **PM session reads code** → understands context, chooses slug, decides approach
5. **PM session spawns Dev session** → Agent SDK (full permissions, Dev persona)
6. **Dev session works** → full pipeline in single process, PM session nudges between stages
7. **PM session composes delivery** → persona-voiced message → Telegram

## Architectural Impact

- **Refactored model**: `AgentSession` — single Popoto model with `session_type` discriminator ("chat" or "dev"). No inheritance (Popoto limitation). Factory methods `create_chat()` and `create_dev()` enforce field contracts.
- **PM session (session_type="chat")**: Read-only Agent SDK session, PM persona. Owns Telegram conversation, orchestrates work.
- **Dev session (session_type="dev")**: Full-permission Agent SDK session, Dev persona. Does the actual coding work. Spawned by PM session via Agent tool.
- **Interface changes**: Observer collapses into PM session's orchestration logic. Both session types can be steered via steering messages.
- **Coupling reduction**: PM session ↔ Dev session is a clean parent/child. Both steerable.
- **Data ownership**: PM session owns Telegram conversation state (steering messages, delivery). Dev session owns execution state (sdlc_stages, slug, artifacts).
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
| Redis running | `redis-cli ping` | AgentSession models use Redis via Popoto |
| Tests pass on main | `pytest tests/unit/ -x -q` | Clean baseline before refactor |
| #458 merged first | `gh issue view 458 --json state -q .state` returns CLOSED | Budget removal simplifies Observer |

## Solution

### Key Elements

- **AgentSession base**: Shared Popoto model for any Agent SDK session (session_id, claude_session_uuid, status, created_at). Both PM session and Dev session inherit from it.
- **PM session**: Read-only Agent SDK session with PM persona. Owns the Telegram conversation, reads code to understand context, chooses slug, decides what to do, spawns/steers Dev sessions, composes delivery messages in persona voice.
- **Dev session**: Full-permission Agent SDK session with Dev persona. Does the actual coding work. Runs full SDLC pipeline if needed. Steered by its parent PM session (not by humans directly).
- **Steering model**: Humans steer PM sessions (via Telegram replies). PM sessions steer Dev sessions (via the PM persona's orchestration logic — the Observer role is absorbed here).
- **Per-chat-group queue**: Each Telegram chat_id gets its own serial queue. Different groups run in parallel.

### Flow

```
Message arrives → PM session created (per chat_id queue) →
  PM session (read-only, PM persona):
    reads code, checks issue, chooses slug, decides approach →
    spawns Dev session (full permissions, Dev persona) →
  Dev session works full pipeline (ISSUE→PLAN→BUILD→TEST→...→MERGE) →
  PM session monitors, nudges between stages →
  PM session composes delivery message in persona voice → Telegram
```

**Human steers mid-pipeline:** Telegram reply → steering message on PM session → PM session decides whether/how to steer its active Dev session

**~7-second window:** Rapid follow-up messages within ~7s of PM session creation become steering automatically (existing behavior preserved).

### Technical Approach

#### Phase 1: Model Refactor
- Refactor `AgentSession` in `models/agent_session.py` — add `session_type` discriminator, PM/Dev session fields, factory methods
- No inheritance (Popoto doesn't support it) — single model, discriminated by `session_type`
- Add derived properties: is_chat, is_dev, is_sdlc, current_stage, branch_name, plan_path
- Remove fields that moved or are obsolete

#### Phase 2: Queue Rekey
- Change queue key from `project_key` to `chat_id`
- Update worker loop to manage per-chat-group workers
- Steering messages route to PM session, not Dev session

#### Phase 3: PM session as Orchestrator
- PM session is an Agent SDK session (read-only permissions, PM persona)
- Absorbs Observer's role: reads code, decides approach, spawns Dev sessions
- Composes delivery messages (absorbs summarizer's formatting role into persona voice)
- Classification happens once inside PM session (no double classification)

#### Phase 4: Single-Session Dev session Pipeline
- Rewrite SDLC prompt: full pipeline spec instead of "invoke /sdlc"
- Remove re-enqueue loop between stages
- Add progress hook (PostToolUse) that PM session monitors for stage transitions
- Remove auto-continue caps (PM session manages continuation)

#### Phase 5: Cleanup
- Remove old Observer (bridge/observer.py)
- Remove budget system (#458)
- Remove delete-and-recreate pattern
- Remove double classification in sdk_client.py
- Remove playlist concept — messages start and end with PM sessions
- Update all consumers

### Data Models

**Popoto ORM limitation:** Popoto does not support model inheritance — parent fields are lost on reload because they're not in the child's `_meta.field_names`. Instead, we use a **single model with a discriminator field** and Python-level class methods for type-specific behavior.

```python
class AgentSession(Model):
    """Single model for all Agent SDK sessions. Discriminated by session_type."""
    session_id = AutoKeyField()
    session_type = Field()             # "chat" or "dev"
    claude_session_uuid = Field(null=True)  # for resume
    status = Field()                   # pending → running → completed/failed
    created_at = Field()

    # PM session fields (null when session_type="dev")
    chat_id = Field(null=True)         # Telegram chat → queue key
    message_id = Field(null=True)      # Telegram message that created this
    sender_name = Field(null=True)
    message_text = Field(null=True)
    project_key = Field(null=True)
    result_text = Field(null=True)     # what was delivered to Telegram

    # Dev session fields (null when session_type="chat")
    parent_chat_session_id = Field(null=True)  # logical FK → PM session
    sdlc_stages = Field(null=True)     # JSON dict, null if not SDLC
    slug = Field(null=True)            # derives branch, plan path, worktree
    artifacts = Field(null=True)       # JSON: {issue_url, plan_url, pr_url}
```

**Python-level type helpers** (not ORM inheritance):
```python
# Convenience constructors
PM session = AgentSession  # Factory methods: AgentSession.create_chat(...)
Dev session = AgentSession   # Factory methods: AgentSession.create_dev(...)

# Derived properties
@property
def is_chat(self) -> bool:
    return self.session_type == "chat"

@property
def is_dev(self) -> bool:
    return self.session_type == "dev"

@property
def is_sdlc(self) -> bool:
    return self.sdlc_stages is not None

@property
def current_stage(self) -> str | None:
    # first stage with status "in_progress"

@property
def branch_name(self) -> str | None:
    return f"session/{self.slug}" if self.slug else None

@property
def plan_path(self) -> str | None:
    return f"docs/plans/{self.slug}.md" if self.slug else None
```

### How PM session Spawns Dev session

PM session runs as a Claude Code process with `permission_mode="plan"` (read-only). To spawn a Dev session:

1. Define a `dev-session` agent in `agent/agent_definitions.py` with `tools=None` (all tools, full permissions)
2. PM session invokes it via the **Agent tool** built into Claude Code
3. The Agent tool spawns a subprocess Claude Code instance with full write access
4. The bridge registers the Dev session in Redis with `parent_chat_session_id` pointing back

```python
# In agent_definitions.py
definitions["dev-session"] = AgentDefinition(
    description="Full-permission developer session for code changes",
    prompt=load_dev_session_prompt(),
    tools=None,  # All tools — full permissions
    model=None,  # Inherit from parent
)
```

### Steering Model

```
Human → (Telegram reply) → PM session
  PM session → (PM persona orchestration) → Dev session
```

- **PM session steered by:** human messages (Telegram replies within ~7s window, or explicit reply-to)
- **Dev session steered by:** its parent PM session's PM persona (not humans directly)
- Both use bounded Redis Lists for steering message queues (capped at 10, oldest dropped on overflow)

### Queue Implementation

Use a **Redis List** keyed as `chat_queue:{chat_id}` for the queue (outside Popoto), with the PM session model in Popoto for metadata. The list holds session IDs; dequeue is atomic `RPOP chat_queue:{chat_id}`, then load `PM session.get(session_id)`. This separates ordering (list) from data (hash) cleanly and gives atomic dequeue without application-level locking.

### Referential Integrity Strategy

Redis/Popoto has no FK enforcement. Integrity is maintained by convention:
- `parent_chat_session_id` on dev sessions is a logical FK; orphan detection runs in the watchdog sweep
- Sessions are never deleted — they transition to terminal states and are garbage-collected by TTL
- PM session finds its Dev sessions via `AgentSession.query.filter(parent_chat_session_id=self.session_id)`
- Single model means all sessions are queryable as `AgentSession.query.filter(...)` regardless of type

### Session Creation Contract

**PM sessions** are created by the **bridge handler** when a message arrives:
1. Handler creates PM session with chat_id, message_text, sender_name
2. PM session pushed to `chat_queue:{chat_id}`

**Dev sessions** are created exclusively by the **PM session** (PM persona) during its Agent SDK execution:
1. PM session reads code, understands context, chooses slug
2. PM session spawns Dev session with `parent_chat_session_id=self.session_id`
3. Dev session runs with full permissions (Dev persona)

Human messages arriving mid-pipeline route as **steering messages on the PM session**. The PM session decides whether/how to steer its active Dev session.

If a Dev session crashes, the PM session can spawn a **new** Dev session to continue from the last completed stage. The crashed Dev session remains as a failed record.

### Steering Message Safety

Steering messages use a **bounded Redis List** (`LPUSH` + `LTRIM` to cap at 10) keyed as `steering:{job_id}`. The active session reads via `RPOP` during PostToolUse hooks. This prevents unbounded accumulation during long-running sessions. If the buffer is full, the oldest unread steering message is dropped (human can re-send).

### Session Liveness

Long-running sessions must have a liveness mechanism:
- **Per-API-call timeout**: `asyncio.wait_for` on each Claude SDK call (existing `SDK_INACTIVITY_TIMEOUT_SECONDS`, default 300s)
- **Session max lifetime**: 60 minutes hard cap; if exceeded, Dev session is killed and PM session can retry
- **Heartbeat**: Activity-based stall detection (from #440) writes timestamps; watchdog checks for staleness

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Observer deterministic path: test all stop_reason values produce correct steer/deliver decision
- [ ] PM session worker: test Dev session spawn failure → PM session marked failed, error delivered to Telegram
- [ ] Dev session resume: test crash mid-pipeline → PM session spawns new Dev session, continues from last completed stage

### Empty/Invalid Input Handling
- [ ] PM session with empty message_text → still created, PM persona decides how to handle
- [ ] Session with null sdlc_stages → treated as non-SDLC, no stage nudging
- [ ] Observer receives empty session output → deliver with "(empty output)" fallback

### Error State Rendering
- [ ] Failed Dev session → PM session delivers error message to Telegram with context
- [ ] Stall detection fires → Dev session killed, PM session informed, delivers partial output

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
- [ ] `tests/unit/test_sdlc_playlist.py` (11 tests) — DELETE: playlist concept removed
- [ ] `tests/unit/test_work_request_classifier.py` (16 tests) — UPDATE: classification happens inside PM session
- [ ] `tests/unit/test_sdlc_env_vars.py` (10 tests) — UPDATE: env vars set once, not per-stage
- [ ] `tests/unit/test_sdlc_mode.py` (6 tests) — UPDATE: is_sdlc derived from sdlc_stages on session

**UPDATE (model changes):**
- [ ] `tests/unit/test_session_status.py` (15 tests) — UPDATE: status tracked on PM session and Dev session separately
- [ ] `tests/unit/test_session_tags.py` (33 tests) — UPDATE: tags may move to PM session model
- [ ] `tests/unit/test_model_relationships.py` (30 tests) — UPDATE: new PM session → Dev session relationship
- [ ] `tests/unit/test_job_hierarchy.py` (22 tests) — REPLACE: hierarchy uses PM/Dev session models
- [ ] `tests/unit/test_pipeline_state_machine.py` (49 tests) — UPDATE: state machine reads from session.sdlc_stages
- [ ] `tests/unit/test_pipeline_integrity.py` (30 tests) — UPDATE: integrity checks use new models
- [ ] `tests/integration/test_agent_session_lifecycle.py` (58 tests) — REPLACE: lifecycle split across PM session + Dev session
- [ ] `tests/integration/test_stage_aware_auto_continue.py` (39 tests) — REPLACE: stage progression is internal, not auto-continue
- [ ] `tests/integration/test_enqueue_continuation.py` (29 tests) — DELETE: no re-enqueue loop
- [ ] `tests/integration/test_steering.py` (32 tests) — UPDATE: steering goes through PM session → Dev session
- [ ] `tests/integration/test_job_queue_race.py` (13 tests) — UPDATE: queue keyed by chat_id
- [ ] `tests/integration/test_job_scheduler.py` (21 tests) — UPDATE: scheduler uses PM session model
- [ ] `tests/e2e/test_message_pipeline.py` (36 tests) — REPLACE: full pipeline flow changed
- [ ] `tests/e2e/test_session_continuity.py` (12 tests) — UPDATE: continuity via PM session + Dev session resume

**Estimated test impact: ~600 tests across 24 files need changes.**

## Rabbit Holes

- **Migrating existing Redis data** — Don't. Old AgentSession records can be left as-is or bulk-deleted. No migration of live data.
- **Making the Observer an LLM "sometimes"** — Deterministic only. If it can't decide, deliver to human. No "smart fallback."
- **Per-stage budget tracking** — Budget is being removed (#458). Don't add per-stage cost tracking.
- **Rewriting sub-skills** — /do-plan, /do-build, etc. are unchanged. Only the orchestration layer changes.
- **Multi-Dev session parallelism** — A PM session spawning parallel Dev sessions (e.g., BUILD + TEST simultaneously) is a future concern. Keep it serial for now.

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

### Race 1: Steering message arrives while Dev session is between stages
**Location:** PM session.steering_messages, PM session's PM orchestration
**Trigger:** Human sends follow-up at the exact moment PM session is deciding next stage
**Data prerequisite:** Steering message must be in PM session's queue before PM reads it
**State prerequisite:** PM session must be in running state
**Mitigation:** PM session reads steering_messages atomically from Redis List before making decisions. Redis operations are single-threaded.

### Race 2: Two messages from same chat group arrive near-simultaneously
**Location:** Chat queue per chat_id
**Trigger:** User sends two messages in rapid succession
**Data prerequisite:** First PM session must be enqueued before second is created
**State prerequisite:** Queue must serialize correctly
**Mitigation:** Per-chat_id queue with atomic `RPOP`. Second message creates a separate PM session that waits in queue. Deduplication logic in handler prevents true duplicates.

### Race 3: TOCTOU on session lookup + steering injection
**Location:** Active session registry, steering message injection
**Trigger:** Human sends follow-up at exact moment session completes and is removed from registry
**Data prerequisite:** Session must be registered as active before steering check
**State prerequisite:** Session must still be running when steering message is injected
**Mitigation:** Per-chat_id `asyncio.Lock` guards the check-and-inject as atomic. Session transitions through `ACTIVE → DRAINING → DONE`; messages arriving during DRAINING are re-queued as new Jobs rather than steered.

### Race 4: Concurrent worker ticks dequeue same PM session
**Location:** Worker loop, chat queue
**Trigger:** Previous worker tick slow, next fires before completion
**Data prerequisite:** PM session must be in queue
**State prerequisite:** Only one worker should process a given PM session
**Mitigation:** Atomic `RPOP` on Redis List guarantees exactly-once dequeue. PM session status transitions from `pending → running` immediately after pop, before any async work begins.

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

- [ ] Create `docs/features/pm-dev-session-architecture.md` describing the PM/Dev session split and lifecycle
- [ ] Update `docs/features/observer-agent.md` to reflect deterministic Observer
- [ ] Update `docs/features/pipeline-graph.md` if Observer integration changes
- [ ] Update `CLAUDE.md` system architecture diagram
- [ ] Archive or update `docs/features/sdlc-enforcement.md`
- [ ] Add entry to `docs/features/README.md` index table

## Success Criteria

- [ ] `AgentSession` model has `session_type` discriminator with factory methods `create_chat()` and `create_dev()`
- [ ] PM session (session_type="chat") fields: chat_id, message_id, sender_name, message_text, project_key, result_text
- [ ] Dev session (session_type="dev") fields: parent_chat_session_id, sdlc_stages, slug, artifacts
- [ ] `dev-session` agent defined in `agent_definitions.py` with `tools=None` (full permissions)
- [ ] Chat queue is keyed by `chat_id`
- [ ] Full SDLC pipeline (issue → merge) completes in a single Dev session
- [ ] PM session (PM persona) orchestrates without a separate Observer component
- [ ] Classification happens inside PM session (no double classification)
- [ ] Budget system fully removed (#458)
- [ ] All tests pass (unit, integration, e2e)
- [ ] Bridge processes messages correctly after deploy
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: model-builder
  - Role: Create AgentSession base, PM session, Dev session models and relationships
  - Agent Type: builder
  - Resume: true

- **Builder (queue)**
  - Name: queue-builder
  - Role: Rekey queue to chat_id, update worker loop, steering routes through PM session
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

### 2. Refactor AgentSession model
- **Task ID**: build-models
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_model_relationships.py -x -q` (create)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `session_type` discriminator field ("chat" or "dev") to AgentSession
- Add PM session fields (chat_id, message_id, sender_name, etc.) as nullable
- Add Dev session fields (parent_chat_session_id, sdlc_stages, slug, artifacts) as nullable
- Add factory methods: `AgentSession.create_chat(...)`, `AgentSession.create_dev(...)`
- Add derived properties: is_chat, is_dev, is_sdlc, current_stage, branch_name, plan_path
- Add `dev-session` agent definition in `agent_definitions.py` with `tools=None`
- Remove obsolete fields from old AgentSession

### 4. Rekey queue to chat_id
- **Task ID**: build-queue-rekey
- **Depends On**: build-models
- **Validates**: `pytest tests/integration/test_job_queue_race.py tests/integration/test_job_scheduler.py -x -q`
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: false
- Change queue key from project_key to chat_id using Redis List (`chat_queue:{chat_id}`)
- Update worker loop to pop PM sessions from per-chat_id queues
- Steering messages route to PM session via bounded Redis List
- Remove delete-and-recreate pattern — sessions transition to terminal states, never deleted

### 5. Implement PM session as orchestrator
- **Task ID**: build-chat-session-orchestrator
- **Depends On**: build-queue-rekey
- **Validates**: `pytest tests/unit/test_observer.py -x -q` (rewritten)
- **Assigned To**: session-builder
- **Agent Type**: builder
- **Parallel**: false
- PM session spawns as Agent SDK session (read-only, PM persona)
- Absorbs Observer's role: reads code, decides approach, spawns Dev sessions
- Absorbs summarizer's delivery formatting into persona voice
- Classification happens inside PM session (remove double classification from routing.py + sdk_client.py)
- Remove old Observer (bridge/observer.py)
- Remove auto-continue caps (MAX_AUTO_CONTINUES, MAX_AUTO_CONTINUES_SDLC)

### 6. Implement single-Dev session SDLC pipeline
- **Task ID**: build-single-dev-session
- **Depends On**: build-chat-session-orchestrator
- **Validates**: `pytest tests/unit/test_sdlc_mode.py tests/unit/test_sdlc_env_vars.py -x -q`
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite SDLC prompt: full pipeline spec (via /sdlc skill) instead of "invoke /sdlc immediately" single-stage
- Dev session works through all stages in single process
- PM session monitors Dev session output, nudges between stages
- Remove re-enqueue loop and _enqueue_continuation
- Remove playlist concept

### 7. Update test suite
- **Task ID**: build-tests
- **Depends On**: build-single-dev-session
- **Validates**: `pytest tests/ -x -q`
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Delete tests for removed functionality (auto-continue, LLM Observer, enqueue_continuation, playlist)
- Rewrite tests for PM/Dev session models
- Rewrite e2e pipeline tests for single-Dev session flow
- Ensure all 24 affected test files are updated

### 8. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-tests
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify PM session → Dev session relationship works
- Verify per-chat_id queue serialization
- Verify full SDLC pipeline completes in single Dev session
- Verify steering messages route through PM session to active Dev session
- Verify no separate Observer component exists

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/pm-dev-session-architecture.md`
- Update or remove `docs/features/observer-agent.md` (Observer absorbed into PM session)
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
| Model has discriminator | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'session_type')"` | exit code 0 |
| Factory methods exist | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'create_chat')"` | exit code 0 |
| Dev agent defined | `grep -c 'dev-session' agent/agent_definitions.py` | output contains 1 |
| No budget refs | `grep -rn 'max_budget_usd\|budget_exceeded\|COST_WARN' agent/ bridge/ --include='*.py'` | exit code 1 |
| No double classify | `grep -cn 'classify_work_request' agent/sdk_client.py` | output contains 0 |
| No separate Observer | `test ! -f bridge/observer.py` | exit code 0 |
| Queue uses chat_id | `grep -n 'chat_queue' agent/job_queue.py \| head -1` | output contains chat_queue |

## Migration Strategy

### In-Flight Session Handling
On deploy, restart the bridge. Any in-flight sessions are abandoned (existing crash recovery handles this). Old AgentSession records in Redis are harmless — they use different key patterns and won't collide with new PM/Dev session records. No data migration needed.

### Queue Cutover
Old queue keys (`queue:{project_key}`) will be empty after restart since workers drain on shutdown. New queue keys (`job_queue:{chat_id}`) start fresh. No messages lost because the bridge only enqueues after restart.

### Rollback Path
If bugs surface after Phase 1 (model split): revert the commit, restart bridge. Old AgentSession code paths still work because the model file is restored. New PM/Dev session records in Redis are orphaned but harmless (TTL cleanup). Each phase is independently revertable via git revert + restart.

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
| CONCERN | data-architect | job_type must be authoritative, never re-derived | Addressed: SDLC is a Dev session property (sdlc_stages != null). PM session doesn't pre-classify. |
| CONCERN | data-architect | No session sequence numbering for "current session" lookup | Addressed: PM session queries Dev sessions by parent_chat_session_id; latest by created_at. |
| CONCERN | data-architect | No concurrent-dequeue protection specified | Addressed: atomic `RPOP` on Redis List guarantees exactly-once dequeue. |

## Resolved Questions

1. **Keep /sdlc skill.** It remains the ground truth for the Observer (alongside the pipeline graph). Used manually in Claude Code sessions and by the Observer to steer sessions. Rewritten as full pipeline spec, not single-stage router.

2. **Remove "playlist" concept entirely.** Messages start and end with Jobs — no remaining connection between Jobs via playlist queues. If an agent needs to send a Telegram message (e.g., to queue the next issue), it uses the Telegram skill like any other tool. The playlist feature (#450) is deprecated by this redesign.

3. **Summarizer merges with persona message writing.** The summarizer's formatting role is absorbed into the persona's message-writing capability. Each persona (Dev, PM) has its own voice for composing delivery messages. The summarizer as a separate component is deprecated.

4. **Chat messages always spawn new PM sessions.** Every new message creates a new PM session. The ~7-second window after a PM session starts allows rapid follow-up messages to become steering automatically (existing behavior). Reply-to messages are always steering for the referenced PM session.

5. **Slugs are agent-created, not user-specified.** A message cannot "arrive for an existing slug" — the Dev session agent writes the slug during execution. Reply-to messages are steering for the parent PM session, not slug-based routing.
