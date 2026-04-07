---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-21
tracking: https://github.com/tomcounsell/ai/issues/459
last_comment_id:
---

# SDLC Redesign: ChatSession/DevSession Split, Single-Session Pipeline, Observer Simplification

## Problem

A Telegram message like "SDLC issue 123" passes through 12+ components before producing a result: Telegram handler → routing classifier → intent classifier → session manager → Redis queue → worker → re-classifier → CWD switch → message enricher → Claude Code spawn → /sdlc skill → sub-skill → Observer (4-phase LLM) → re-enqueue → repeat 7x → summarizer → formatter → Telegram.

**Current behavior:**
1. Intent is classified twice (routing.py and sdk_client.py) — these can disagree
2. `AgentSession` conflates the human's request with the Claude Code execution — no clean ChatSession/DevSession separation. The delete-and-recreate pattern makes record IDs unstable.
3. A 7-stage SDLC pipeline spawns Claude Code 7+ times. Each spawn: process creation, prompt loading, context rebuild.
4. The Observer is a 4-phase LLM with tools (`read_session`, `update_session`, `enqueue_continuation`, `deliver_to_telegram`) to make mostly deterministic routing decisions.
5. Queue is per-project — two chat groups for the same project block each other.

**Desired outcome:**
```
Message → classify (lightweight, no Claude Code) →
  If simple Q&A → single Agent SDK session handles directly →
  If SDLC/complex → ChatSession created (read-only, PM persona) → Queue (per chat_id) →
    ChatSession reads code, chooses slug, decides approach →
    Spawns DevSession (full permissions, Dev persona) →
    DevSession works full pipeline → ChatSession nudges between stages →
    ChatSession composes delivery → Telegram
```
- Both are Agent SDK sessions sharing an `AgentSession` model with `session_type` discriminator.
- `ChatSession` (read-only, PM persona) owns the Telegram conversation, orchestrates.
- `DevSession` (full permissions, Dev persona) does the actual work.
- Simple messages (Q&A, greetings) skip the ChatSession/DevSession pattern — handled by a single session with no orchestration overhead.
- Claude Code spawned once per unit of work, not per stage.
- Queues are per chat group.

## Spike Results

### spike-1: Can Agent SDK create read-only sessions?
- **Assumption**: "We can spawn ChatSession as a read-only Claude Code process"
- **Method**: code-read
- **Finding**: YES. SDK supports `permission_mode="plan"`, `allowed_tools=[...]`, and `disallowed_tools=[...]`. The validator agent already uses `_READ_ONLY_TOOLS` list. Multiple orthogonal restriction mechanisms available.
- **Confidence**: high
- **Impact on plan**: ChatSession can use `permission_mode="plan"` with `disallowed_tools=["Write", "Edit", "NotebookEdit"]`

### spike-2: Does Popoto ORM support model inheritance?
- **Assumption**: "We can do `class ChatSession(AgentSession)` with Popoto"
- **Method**: code-read + prototype
- **Finding**: NO. Popoto's metaclass (`ModelBase`) does not inherit parent fields into child `_meta.field_names`. After save+reload, parent fields are silently lost. The `# todo: handle multiple inheritance` comment at line 366 confirms this is a known limitation.
- **Confidence**: high
- **Impact on plan**: BLOCKER resolved — switched to single model with `session_type` discriminator field instead of class inheritance. Factory methods provide type safety at the Python level.

### spike-3: How does ChatSession spawn DevSession?
- **Assumption**: "A read-only Claude Code session can programmatically spawn a full-permission session"
- **Method**: code-read
- **Finding**: YES. Claude Code's built-in Agent tool can invoke agents defined in `agent_definitions.py`. A new `dev-session` agent with `tools=None` (all tools) gives full write permissions. The validator/code-reviewer agents already demonstrate the pattern of tool-restricted agents. ChatSession invokes `@Agent dev-session` which spawns a subprocess with full access.
- **Confidence**: high
- **Impact on plan**: DevSession spawning works via existing Agent tool infrastructure. No new bridge tools or MCP servers needed.

### spike-4: How does the bridge register DevSession records in Redis?
- **Assumption**: "The bridge can observe DevSession creation and register it in Redis with parent linkage"
- **Method**: code-read of Agent tool and hook infrastructure
- **Finding**: The bridge cannot directly observe what happens inside a Claude Code process's Agent tool invocations. Claude Code's Agent tool spawns a subprocess — the bridge has no hook into that subprocess creation. However, two viable mechanisms exist:
  1. **PostToolUse hook on the ChatSession process**: The bridge already monitors tool calls via the SDK's streaming events. When ChatSession invokes the Agent tool with `subagent_type="dev-session"`, the bridge can intercept this event and pre-register the DevSession in Redis.
  2. **MCP tool for self-registration**: Expose a `register_dev_session` tool via MCP that the DevSession calls on startup to register itself and establish the parent linkage.
- **Confidence**: medium — option 1 is cleaner but depends on Agent tool events being visible in the SDK stream. Needs prototype validation.
- **Impact on plan**: Added task to prototype and validate the registration mechanism before building the full ChatSession orchestrator.

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

### Target (simple messages — 4 hops)
1. **Telegram** → handler() receives message
2. **Lightweight classify** → routing.py determines Q&A vs SDLC (existing Ollama/Haiku classifier, no Claude Code)
3. **Single session** → Agent SDK session handles Q&A directly (current behavior, no ChatSession overhead)
4. **Delivery** → Telegram message sent

### Target (SDLC/complex — 7 hops)
1. **Telegram** → handler() receives message
2. **Lightweight classify** → routing.py determines SDLC/complex work
3. **ChatSession created** → queued per chat_id
4. **Worker** → pops ChatSession, starts Agent SDK (read-only, PM persona)
5. **ChatSession reads code** → understands context, chooses slug, decides approach
6. **ChatSession spawns DevSession** → Agent SDK (full permissions, Dev persona)
7. **DevSession works** → full pipeline in single process, ChatSession nudges between stages
8. **ChatSession composes delivery** → persona-voiced message → Telegram

## Architectural Impact

- **Refactored model**: `AgentSession` — single Popoto model with `session_type` discriminator ("chat", "dev", or "simple"). No inheritance (Popoto limitation). Factory methods `create_chat()`, `create_dev()`, and `create_simple()` enforce field contracts.
- **ChatSession (session_type="chat")**: Read-only Agent SDK session, PM persona. Owns Telegram conversation, orchestrates work. Only used for SDLC/complex work.
- **DevSession (session_type="dev")**: Full-permission Agent SDK session, Dev persona. Does the actual coding work. Spawned by ChatSession via Agent tool.
- **Simple session (session_type="simple")**: Current single-session behavior for Q&A, greetings, etc. No ChatSession overhead.
- **Interface changes**: Observer collapses into deterministic routing logic within ChatSession. Both session types can be steered via steering messages.
- **Coupling reduction**: ChatSession ↔ DevSession is a clean parent/child. Both steerable.
- **Data ownership**: ChatSession owns Telegram conversation state (steering messages, delivery). DevSession owns execution state (sdlc_stages, slug, artifacts).
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
| Budget removal merged | `grep -rn 'max_budget_usd\|budget_exceeded' agent/ bridge/ --include='*.py' \| grep -v types.py` returns empty | Budget system already removed on `session/remove_budget_system` branch; merge before starting |

## Solution

### Key Elements

- **AgentSession base**: Shared Popoto model for any Agent SDK session (session_id, claude_session_uuid, status, created_at). ChatSession, DevSession, and simple sessions all use this model, discriminated by `session_type`.
- **ChatSession**: Read-only Agent SDK session with PM persona. Owns the Telegram conversation, reads code to understand context, chooses slug, decides what to do, spawns/steers DevSessions, composes delivery messages in persona voice. Only used for SDLC/complex work.
- **DevSession**: Full-permission Agent SDK session with Dev persona. Does the actual coding work. Runs full SDLC pipeline if needed. Steered by its parent ChatSession (not by humans directly).
- **Simple session**: For Q&A and non-SDLC messages. Single Agent SDK session, no orchestration. Preserves current fast-path behavior.
- **Steering model**: Humans steer ChatSessions (via Telegram replies). ChatSessions steer DevSessions (via the PM persona's orchestration logic — the Observer role is absorbed here).
- **Per-chat-group queue**: Each Telegram chat_id gets its own serial queue. Different groups run in parallel.
- **DevSession registration**: Bridge intercepts Agent tool invocations via SDK streaming events (PostToolUse hook). When ChatSession invokes `dev-session` agent, bridge pre-registers the DevSession record in Redis with `parent_chat_session_id`. Fallback: MCP tool for self-registration if SDK events don't expose Agent tool calls.

### Flow

```
Message arrives → lightweight classify (routing.py, no Claude Code) →

  If Q&A/simple:
    Simple session created → single Agent SDK session → delivery → Telegram

  If SDLC/complex:
    ChatSession created (per chat_id queue) →
      ChatSession (read-only, PM persona):
        reads code, checks issue, chooses slug, decides approach →
        spawns DevSession (full permissions, Dev persona) →
      DevSession works full pipeline (ISSUE→PLAN→BUILD→TEST→...→MERGE) →
      ChatSession monitors, nudges between stages →
      ChatSession composes delivery message in persona voice → Telegram
```

**Human steers mid-pipeline:** Telegram reply → steering message on ChatSession → ChatSession decides whether/how to steer its active DevSession

**~7-second window:** Rapid follow-up messages within ~7s of ChatSession creation become steering automatically (existing behavior preserved).

### Technical Approach

#### Phase 1: Model Refactor
- Refactor `AgentSession` in `models/agent_session.py` — add `session_type` discriminator, ChatSession/DevSession/simple fields, factory methods
- No inheritance (Popoto doesn't support it) — single model, discriminated by `session_type`
- Add derived properties: is_chat, is_dev, is_simple, is_sdlc, current_stage, branch_name, plan_path
- Remove fields that moved or are obsolete
- **Update tests for model changes in this phase**

#### Phase 2: DevSession Registration Prototype
- Prototype the bridge mechanism for detecting Agent tool invocations via SDK streaming events
- Validate that PostToolUse hook can see Agent tool calls with the subagent_type parameter
- If not visible: implement MCP tool fallback (`register_dev_session`)
- **This must be validated before Phase 4 (ChatSession as orchestrator)**

#### Phase 3: Queue Rekey
- Change queue key from `project_key` to `chat_id`
- Update worker loop to manage per-chat-group workers
- Steering messages route to ChatSession, not DevSession
- Wrap hot-path Popoto calls in `asyncio.to_thread()` (session creation, queue operations) to reduce event loop blocking
- **Update tests for queue changes in this phase**

#### Phase 4: ChatSession as Orchestrator
- ChatSession is an Agent SDK session (read-only permissions, PM persona)
- Absorbs Observer's role: reads code, decides approach, spawns DevSessions
- Uses DevSession registration mechanism from Phase 2
- Composes delivery messages (absorbs summarizer's formatting role into persona voice)
- Classification happens once inside ChatSession (no double classification)
- Simple messages bypass ChatSession entirely (fast-path preserved)
- **Update tests for Observer/ChatSession changes in this phase**

#### Phase 5: Single-Session DevSession Pipeline
- Rewrite SDLC prompt: full pipeline spec instead of "invoke /sdlc"
- Remove re-enqueue loop between stages
- Add progress hook (PostToolUse) that ChatSession monitors for stage transitions
- Remove auto-continue caps (ChatSession manages continuation)
- **Update tests for pipeline changes in this phase**

#### Phase 6: Cleanup
- Remove old Observer (bridge/observer.py)
- Remove delete-and-recreate pattern
- Remove double classification in sdk_client.py
- Remove playlist concept — messages start and end with ChatSessions or simple sessions
- **Final test sweep for any remaining gaps**

### Observer Decision Table

The Observer's current 4-phase LLM decision process collapses into deterministic logic within ChatSession. Here is the complete decision table:

| stop_reason | session_state | sdlc_stage | has_remaining_stages | has_steering_msg | Action |
|-------------|---------------|------------|---------------------|-----------------|--------|
| `rate_limited` | running | any | any | any | Steer: "wait 60s, resume where you left off" |
| `end_turn` | running | any | yes | yes | Steer: deliver steering msg, then "continue to next stage" |
| `end_turn` | running | any | yes | no | Steer: "stage complete, proceed to {next_stage}" |
| `end_turn` | running | any | no | any | Deliver: compose final message to Telegram |
| `end_turn` | running | TEST | yes | no | Steer: if test failed → "proceed to PATCH"; if passed → "proceed to {next_stage}" |
| `end_turn` | running | REVIEW | yes | no | Steer: if blockers → "proceed to PATCH"; if approved → "proceed to {next_stage}" |
| `timeout` | running | any | any | any | Kill DevSession. ChatSession delivers partial output + error context to Telegram |
| `error` | running | any | any | any | Kill DevSession. ChatSession can retry (new DevSession) from last completed stage, up to 2 retries. After 2 retries: deliver error to Telegram |
| any | completed | any | no | any | Deliver: compose final message |
| any | failed | any | any | any | Deliver: compose error message with context |

**Ambiguity rule**: If the deterministic table doesn't match (unexpected stop_reason, corrupted state), deliver to human with raw output. Never fall back to LLM — ambiguity = escalate.

### Data Models

**Popoto ORM limitation:** Popoto does not support model inheritance — parent fields are lost on reload because they're not in the child's `_meta.field_names`. Instead, we use a **single model with a discriminator field** and Python-level class methods for type-specific behavior.

```python
class AgentSession(Model):
    """Single model for all Agent SDK sessions. Discriminated by session_type."""
    session_id = AutoKeyField()
    session_type = Field()             # "chat", "dev", or "simple"
    claude_session_uuid = Field(null=True)  # for resume
    status = Field()                   # pending → running → completed/failed
    created_at = Field()

    # ChatSession fields (null when session_type != "chat")
    chat_id = Field(null=True)         # Telegram chat → queue key
    message_id = Field(null=True)      # Telegram message that created this
    sender_name = Field(null=True)
    message_text = Field(null=True)
    project_key = Field(null=True)
    result_text = Field(null=True)     # what was delivered to Telegram

    # DevSession fields (null when session_type != "dev")
    parent_chat_session_id = Field(null=True)  # logical FK → ChatSession
    sdlc_stages = Field(null=True)     # JSON dict, null if not SDLC
    slug = Field(null=True)            # derives branch, plan path, worktree
    artifacts = Field(null=True)       # JSON: {issue_url, plan_url, pr_url}

    # Simple session fields — uses chat_id, message_id, message_text, project_key
    # (same fields as ChatSession subset, no additional fields needed)
```

**Python-level type helpers** (not ORM inheritance):
```python
# Convenience constructors
ChatSession = AgentSession  # Factory methods: AgentSession.create_chat(...)
DevSession = AgentSession   # Factory methods: AgentSession.create_dev(...)

# Derived properties
@property
def is_chat(self) -> bool:
    return self.session_type == "chat"

@property
def is_dev(self) -> bool:
    return self.session_type == "dev"

@property
def is_simple(self) -> bool:
    return self.session_type == "simple"

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

### How ChatSession Spawns DevSession

ChatSession runs as a Claude Code process with `permission_mode="plan"` (read-only). To spawn a DevSession:

1. Define a `dev-session` agent in `agent/agent_definitions.py` with `tools=None` (all tools, full permissions)
2. ChatSession invokes it via the **Agent tool** built into Claude Code
3. The Agent tool spawns a subprocess Claude Code instance with full write access
4. **Bridge registers the DevSession**: The bridge monitors ChatSession's SDK streaming events. When a PostToolUse event for the Agent tool with `subagent_type="dev-session"` is detected, the bridge creates the DevSession record in Redis with `parent_chat_session_id` pointing back to the ChatSession. If SDK events don't expose Agent tool parameters (validated in Phase 2), the fallback is an MCP tool `register_dev_session(parent_session_id, slug)` that the DevSession calls on startup.

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
Human → (Telegram reply) → ChatSession
  ChatSession → (PM persona orchestration) → DevSession
```

- **ChatSession steered by:** human messages (Telegram replies within ~7s window, or explicit reply-to)
- **DevSession steered by:** its parent ChatSession's PM persona (not humans directly)
- Both use bounded Redis Lists for steering message queues (capped at 10, oldest dropped on overflow)

### Queue Implementation

Use a **Redis List** keyed as `chat_queue:{chat_id}` for the queue (outside Popoto), with the ChatSession model in Popoto for metadata. The list holds session IDs; dequeue is atomic `RPOP chat_queue:{chat_id}`, then load `ChatSession.get(session_id)`. This separates ordering (list) from data (hash) cleanly and gives atomic dequeue without application-level locking.

**Hot-path async wrapping**: Popoto ORM uses synchronous Redis calls. The redesign increases Redis operations per message (session creation, queue ops, steering). Wrap Popoto calls on the hot path in `asyncio.to_thread()` to prevent event loop blocking:
```python
session = await asyncio.to_thread(AgentSession.create_chat, chat_id=chat_id, ...)
await asyncio.to_thread(session.save)
```

### Referential Integrity Strategy

Redis/Popoto has no FK enforcement. Integrity is maintained by convention:
- `parent_chat_session_id` on dev sessions is a logical FK; orphan detection runs in the watchdog sweep
- Sessions are never deleted — they transition to terminal states and are garbage-collected by TTL
- ChatSession finds its DevSessions via `AgentSession.query.filter(parent_chat_session_id=self.session_id)`
- Single model means all sessions are queryable as `AgentSession.query.filter(...)` regardless of type

### Session Creation Contract

**Simple sessions** are created by the **bridge handler** for Q&A/non-SDLC messages:
1. Handler creates simple session with chat_id, message_text, sender_name
2. Processed immediately by a single Agent SDK session (current behavior)

**ChatSessions** are created by the **bridge handler** when SDLC/complex work is detected:
1. Handler creates ChatSession with chat_id, message_text, sender_name
2. ChatSession pushed to `chat_queue:{chat_id}`

**DevSessions** are created exclusively by the **bridge** when it detects ChatSession spawning a dev-session agent:
1. Bridge intercepts Agent tool PostToolUse event for `dev-session` subagent
2. Bridge creates DevSession record in Redis with `parent_chat_session_id=chat_session.session_id`
3. DevSession runs with full permissions (Dev persona)

Human messages arriving mid-pipeline route as **steering messages on the ChatSession**. The ChatSession decides whether/how to steer its active DevSession.

If a DevSession crashes, the ChatSession can spawn a **new** DevSession to continue from the last completed stage. The crashed DevSession remains as a failed record.

### Steering Message Safety

Steering messages use a **bounded Redis List** (`LPUSH` + `LTRIM` to cap at 10) keyed as `steering:{session_id}`. The active session reads via `RPOP` during PostToolUse hooks. This prevents unbounded accumulation during long-running sessions. If the buffer is full, the oldest unread steering message is dropped (human can re-send).

### Session Liveness

Long-running sessions must have a liveness mechanism:
- **Per-API-call timeout**: `asyncio.wait_for` on each Claude SDK call (existing `SDK_INACTIVITY_TIMEOUT_SECONDS`, default 300s)
- **Session max lifetime**: Configurable per session type. DevSession default: 150 minutes (full SDLC pipeline with BUILD + TEST + PATCH cycles can legitimately take 90+ minutes; 150 min allows headroom without being unbounded). ChatSession default: 180 minutes (must outlive its DevSession). Simple session default: 30 minutes.
- **Heartbeat**: Activity-based stall detection (from #440) writes timestamps; watchdog checks for staleness
- **Rationale for limits**: `JOB_TIMEOUT_BUILD` is already 2.5 hours (9000s) in the current system. The 150-minute DevSession limit aligns with observed full-pipeline durations while preventing runaway sessions.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Observer deterministic path: test all (stop_reason, session_state, sdlc_stage) tuples from the decision table produce correct steer/deliver action
- [ ] ChatSession worker: test DevSession spawn failure → ChatSession marked failed, error delivered to Telegram
- [ ] DevSession resume: test crash mid-pipeline → ChatSession spawns new DevSession, continues from last completed stage
- [ ] DevSession registration failure: test bridge can't register DevSession → ChatSession detects orphan, retries registration

### Empty/Invalid Input Handling
- [ ] ChatSession with empty message_text → still created, PM persona decides how to handle
- [ ] Session with null sdlc_stages → treated as non-SDLC, no stage nudging
- [ ] Observer receives empty session output → deliver with "(empty output)" fallback

### Error State Rendering
- [ ] Failed DevSession → ChatSession delivers error message to Telegram with context
- [ ] Stall detection fires → DevSession killed, ChatSession informed, delivers partial output

## Test Impact

Major refactor — nearly all test files touching these components need updates. **Tests are updated per-phase, not batched at the end.**

**DELETE (removed functionality):**
- [ ] `tests/unit/test_auto_continue.py` (22 tests) — DELETE: auto-continue loop eliminated [Phase 5]
- [ ] `tests/unit/test_stop_reason_observer.py` (7 tests) — REPLACE: Observer no longer LLM-based [Phase 4]
- [ ] `tests/unit/test_observer_early_return.py` (18 tests) — DELETE: no LLM Observer to early-return from [Phase 4]
- [ ] `tests/unit/test_observer_message_for_user.py` (11 tests) — DELETE: Observer no longer generates messages [Phase 4]

**REPLACE (new interfaces):**
- [ ] `tests/unit/test_observer.py` (36 tests) — REPLACE: rewrite for deterministic Observer decision table [Phase 4]
- [ ] `tests/unit/test_sdk_client_sdlc.py` (38 tests) — UPDATE: single-session model changes SDK invocation [Phase 5]
- [ ] `tests/unit/test_sdlc_playlist.py` (11 tests) — DELETE: playlist concept removed [Phase 6]
- [ ] `tests/unit/test_work_request_classifier.py` (16 tests) — UPDATE: classification happens inside ChatSession [Phase 4]
- [ ] `tests/unit/test_sdlc_env_vars.py` (10 tests) — UPDATE: env vars set once, not per-stage [Phase 5]
- [ ] `tests/unit/test_sdlc_mode.py` (6 tests) — UPDATE: is_sdlc derived from sdlc_stages on session [Phase 1]

**UPDATE (model changes):**
- [ ] `tests/unit/test_session_status.py` (15 tests) — UPDATE: status tracked on ChatSession and DevSession separately [Phase 1]
- [ ] `tests/unit/test_session_tags.py` (33 tests) — UPDATE: tags may move to ChatSession model [Phase 1]
- [ ] `tests/unit/test_model_relationships.py` (30 tests) — UPDATE: new ChatSession → DevSession relationship [Phase 1]
- [ ] `tests/unit/test_job_hierarchy.py` (22 tests) — REPLACE: hierarchy uses ChatSession/DevSession models [Phase 1]
- [ ] `tests/unit/test_pipeline_state_machine.py` (49 tests) — UPDATE: state machine reads from session.sdlc_stages [Phase 5]
- [ ] `tests/unit/test_pipeline_integrity.py` (30 tests) — UPDATE: integrity checks use new models [Phase 5]
- [ ] `tests/integration/test_agent_session_lifecycle.py` (58 tests) — REPLACE: lifecycle split across ChatSession + DevSession [Phase 4]
- [ ] `tests/integration/test_stage_aware_auto_continue.py` (39 tests) — REPLACE: stage progression is internal, not auto-continue [Phase 5]
- [ ] `tests/integration/test_enqueue_continuation.py` (29 tests) — DELETE: no re-enqueue loop [Phase 5]
- [ ] `tests/integration/test_steering.py` (32 tests) — UPDATE: steering goes through ChatSession → DevSession [Phase 3]
- [ ] `tests/integration/test_job_queue_race.py` (13 tests) — UPDATE: queue keyed by chat_id [Phase 3]
- [ ] `tests/integration/test_job_scheduler.py` (21 tests) — UPDATE: scheduler uses ChatSession model [Phase 3]
- [ ] `tests/e2e/test_message_pipeline.py` (36 tests) — REPLACE: full pipeline flow changed [Phase 5]
- [ ] `tests/e2e/test_session_continuity.py` (12 tests) — UPDATE: continuity via ChatSession + DevSession resume [Phase 4]

**Estimated test impact: ~600 tests across 24 files. Each phase updates its own tests before merging.**

## Rabbit Holes

- **Migrating existing Redis data** — Don't. Old AgentSession records can be left as-is or bulk-deleted. No migration of live data.
- **Making the Observer an LLM "sometimes"** — Deterministic only. If it can't decide, deliver to human. No "smart fallback."
- **Per-stage cost tracking** — Budget is removed. Don't add per-stage cost tracking.
- **Rewriting sub-skills** — /do-plan, /do-build, etc. are unchanged. Only the orchestration layer changes.
- **Multi-DevSession parallelism** — A ChatSession spawning parallel DevSessions (e.g., BUILD + TEST simultaneously) is a future concern. Keep it serial for now.
- **Migrating Popoto to async Redis** — Wrap hot-path calls in `asyncio.to_thread()` for now. Full async migration is separate work.

## Risks

### Risk 1: Long-running single session hits SDK/API limits
**Impact:** Full pipeline in one session could run 90+ minutes. Unknown SDK behavior at that duration.
**Mitigation:** Activity-based stall detection already handles this. Session continuation handles crashes. Configurable max lifetime (150 min default). Test with a real full-pipeline run before shipping.

### Risk 2: Context window exhaustion in single session
**Impact:** A full SDLC pipeline generates a lot of tool output. Could exhaust Claude's context window mid-pipeline, silently dropping earlier context (like plan requirements from the PLAN stage).
**Mitigation:** Claude Code handles context management internally (compression, summarization). Sub-agents for heavy tasks (PR review) keep the main context clean. **Concrete validation required**: Run a real full-pipeline session on a non-trivial issue and measure actual context usage before shipping Phase 5. If compression drops plan requirements, add explicit checkpointing between stages (write stage summaries to disk that later stages re-read).

### Risk 3: Breaking the bridge during incremental migration
**Impact:** Bridge must stay operational throughout. A bad deploy could block all Telegram processing.
**Mitigation:** Phase the work: model split first (backward compatible), then queue rekey, then session changes, then Observer. Each phase is independently deployable. Keep old code paths until new ones are validated.

### Risk 4: Test suite disruption
**Impact:** ~600 tests need changes. Risk of test rot during migration.
**Mitigation:** Each phase updates its own tests before merging. Never merge with failing tests.

### Risk 5: DevSession registration mechanism doesn't work
**Impact:** If SDK streaming events don't expose Agent tool parameters, the bridge can't detect DevSession spawning.
**Mitigation:** Phase 2 (prototype) validates this before any dependent work. MCP tool fallback is a proven pattern (existing tools already use it). Worst case: DevSession self-registers on startup.

## Race Conditions

### Race 1: Steering message arrives while DevSession is between stages
**Location:** ChatSession.steering_messages, ChatSession's PM orchestration
**Trigger:** Human sends follow-up at the exact moment ChatSession is deciding next stage
**Data prerequisite:** Steering message must be in ChatSession's queue before PM reads it
**State prerequisite:** ChatSession must be in running state
**Mitigation:** ChatSession reads steering_messages atomically from Redis List before making decisions. Redis operations are single-threaded.

### Race 2: Two messages from same chat group arrive near-simultaneously
**Location:** Chat queue per chat_id
**Trigger:** User sends two messages in rapid succession
**Data prerequisite:** First ChatSession must be enqueued before second is created
**State prerequisite:** Queue must serialize correctly
**Mitigation:** Per-chat_id queue with atomic `RPOP`. Second message creates a separate ChatSession that waits in queue. Deduplication logic in handler prevents true duplicates.

### Race 3: TOCTOU on session lookup + steering injection
**Location:** Active session registry, steering message injection
**Trigger:** Human sends follow-up at exact moment session completes and is removed from registry
**Data prerequisite:** Session must be registered as active before steering check
**State prerequisite:** Session must still be running when steering message is injected
**Mitigation:** Per-chat_id `asyncio.Lock` guards the check-and-inject as atomic. Session transitions through `ACTIVE → DRAINING → DONE`; messages arriving during DRAINING are re-queued as new ChatSessions rather than steered.

### Race 4: Concurrent worker ticks dequeue same ChatSession
**Location:** Worker loop, chat queue
**Trigger:** Previous worker tick slow, next fires before completion
**Data prerequisite:** ChatSession must be in queue
**State prerequisite:** Only one worker should process a given ChatSession
**Mitigation:** Atomic `RPOP` on Redis List guarantees exactly-once dequeue. ChatSession status transitions from `pending → running` immediately after pop, before any async work begins.

### Race 5: Bridge restart during active sessions — message loss window
**Location:** Queue cutover during bridge restart
**Trigger:** Messages arrive between old process stopping and new process starting (~2s window)
**Data prerequisite:** Telethon must reconnect and fetch missed messages
**State prerequisite:** Old queue keys may have unprocessed items
**Mitigation:** Telethon uses persistent sessions with `pts` tracking — messages received during downtime are fetched on reconnect (not webhooks, so no delivery timeout). New code checks for and drains old queue keys (`queue:{project_key}`) on startup during migration period, re-enqueuing any orphaned items into new `chat_queue:{chat_id}` keys.

## No-Gos (Out of Scope)

- **Parallel session execution** — Sessions spawn serially. Parallel sessions (e.g., running BUILD and DOCS simultaneously) is future work.
- **Standalone PM-only sessions** — ChatSession always orchestrates work (spawns DevSessions or handles steering). It is not a general-purpose PM chatbot. The PM persona is scoped to orchestration and delivery composition.
- **Rewriting sub-skills** — /do-plan, /do-build, /do-test, /do-pr-review, /do-docs, /do-merge are unchanged.
- **Telegram bot API migration** — Keep Telethon. Don't switch to Bot API.
- **Redis → PostgreSQL** — Keep Popoto/Redis. Don't change the storage layer.
- **Multi-project sessions** — One session targets one project. Cross-project orchestration is future work.
- **Full async Popoto migration** — Wrap hot-path calls in `asyncio.to_thread()`, don't rewrite Popoto internals.

## Update System

- No new dependencies or services
- No new config files to propagate
- After deploy: restart bridge (`./scripts/valor-service.sh restart`)
- Old Redis AgentSession records are harmless — no data migration needed
- The update skill itself needs no changes

## Agent Integration

No agent integration required — this is a bridge-internal architectural refactor. The agent (Claude Code) receives messages and uses tools exactly as before. The change is in how sessions are spawned and orchestrated, not in what tools are available.

One change the agent will notice: SDLC sessions receive the full pipeline spec in the initial message instead of being told to "invoke /sdlc immediately." The /sdlc skill itself may be simplified or removed once the pipeline spec is in the prompt.

**DevSession registration** may require a new MCP tool (`register_dev_session`) if SDK streaming events don't expose Agent tool parameters. This would be added to the existing MCP server, not a new server. Validated in Phase 2 before building.

## Documentation

- [ ] Create `docs/features/chat-dev-session-architecture.md` describing the ChatSession/DevSession split and lifecycle
- [ ] Update `docs/features/observer-agent.md` to reflect deterministic Observer decision table
- [ ] Update `docs/features/pipeline-graph.md` if Observer integration changes
- [ ] Update `CLAUDE.md` system architecture diagram
- [ ] Archive or update `docs/features/sdlc-enforcement.md`
- [ ] Add entry to `docs/features/README.md` index table

## Operational Metrics

Post-deploy, measure these to validate the redesign achieved its goals:

| Metric | Current Baseline | Target | How to Measure |
|--------|-----------------|--------|----------------|
| Claude Code spawns per SDLC pipeline | 7+ | 2 (ChatSession + DevSession) | Count `claude_agent_sdk` process spawns per pipeline in logs |
| Wall-clock time for full SDLC pipeline | ~25-40 min (7 spawns × 3-5 min each) | ~20-30 min (single session, no spawn overhead) | Measure ChatSession created_at → completed_at |
| Observer LLM calls per pipeline | 7+ (one per stage) | 0 (deterministic) | Count observer LLM invocations in logs |
| Messages processed per minute (simple Q&A) | Baseline TBD | Same or better (no regression from ChatSession overhead) | Track simple session latency |
| Context window exhaustion incidents | N/A (new risk) | 0 in first week | Monitor for sessions that lose plan requirements mid-pipeline |

## Success Criteria

- [ ] `AgentSession` model has `session_type` discriminator with factory methods `create_chat()`, `create_dev()`, `create_simple()`
- [ ] ChatSession (session_type="chat") fields: chat_id, message_id, sender_name, message_text, project_key, result_text
- [ ] DevSession (session_type="dev") fields: parent_chat_session_id, sdlc_stages, slug, artifacts
- [ ] `dev-session` agent defined in `agent_definitions.py` with `tools=None` (full permissions)
- [ ] DevSession registration mechanism validated and working (bridge detects spawns, creates Redis records)
- [ ] Chat queue is keyed by `chat_id`
- [ ] Full SDLC pipeline (issue → merge) completes in a single DevSession
- [ ] ChatSession (PM persona) orchestrates without a separate Observer component
- [ ] Classification happens once — lightweight classify in routing.py, then ChatSession or simple session
- [ ] Simple Q&A messages handled without ChatSession overhead (fast-path preserved)
- [ ] Observer decision table covers all (stop_reason, session_state, sdlc_stage) tuples
- [ ] All tests pass (unit, integration, e2e)
- [ ] Bridge processes messages correctly after deploy
- [ ] Operational metrics show ≤2 Claude Code spawns per SDLC pipeline
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: model-builder
  - Role: Create AgentSession session_type discriminator, ChatSession/DevSession/simple fields, factory methods, derived properties
  - Agent Type: builder
  - Resume: true

- **Builder (registration)**
  - Name: registration-builder
  - Role: Prototype and validate DevSession registration mechanism (SDK events or MCP fallback)
  - Agent Type: builder
  - Resume: true

- **Builder (queue)**
  - Name: queue-builder
  - Role: Rekey queue to chat_id, update worker loop, add asyncio.to_thread wrapping, steering routes through ChatSession
  - Agent Type: builder
  - Resume: true

- **Builder (session)**
  - Name: session-builder
  - Role: Implement ChatSession orchestrator with deterministic decision table, simple session fast-path
  - Agent Type: builder
  - Resume: true

- **Builder (pipeline)**
  - Name: pipeline-builder
  - Role: Implement single-session SDLC, rewrite prompt, add progress hook
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Remove double classification, delete-and-recreate pattern, playlist, old Observer
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify full pipeline e2e, model relationships, queue behavior, operational metrics
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create/update feature docs, architecture diagrams
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Refactor AgentSession model
- **Task ID**: build-models
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_model_relationships.py tests/unit/test_session_status.py tests/unit/test_job_hierarchy.py tests/unit/test_sdlc_mode.py -x -q`
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `session_type` discriminator field ("chat", "dev", "simple") to AgentSession
- Add ChatSession fields (chat_id, message_id, sender_name, etc.) as nullable
- Add DevSession fields (parent_chat_session_id, sdlc_stages, slug, artifacts) as nullable
- Add factory methods: `AgentSession.create_chat(...)`, `AgentSession.create_dev(...)`, `AgentSession.create_simple(...)`
- Add derived properties: is_chat, is_dev, is_simple, is_sdlc, current_stage, branch_name, plan_path
- Add `dev-session` agent definition in `agent_definitions.py` with `tools=None`
- Remove obsolete fields from old AgentSession
- **Update tests**: test_model_relationships.py, test_session_status.py, test_session_tags.py, test_job_hierarchy.py, test_sdlc_mode.py

### 2. Prototype DevSession registration mechanism
- **Task ID**: build-registration-prototype
- **Depends On**: build-models
- **Validates**: `pytest tests/unit/test_dev_session_registration.py -x -q` (create)
- **Assigned To**: registration-builder
- **Agent Type**: builder
- **Parallel**: false
- Prototype PostToolUse hook that detects Agent tool invocations for `dev-session` subagent
- Test whether SDK streaming events expose Agent tool parameters (subagent_type, prompt)
- If visible: implement bridge-side DevSession registration on Agent tool detection
- If not visible: implement MCP tool `register_dev_session(parent_session_id, slug)` as fallback
- Write tests for the chosen registration mechanism
- **Gate**: This must pass before Phase 4 begins

### 3. Rekey queue to chat_id
- **Task ID**: build-queue-rekey
- **Depends On**: build-models
- **Validates**: `pytest tests/integration/test_job_queue_race.py tests/integration/test_job_scheduler.py tests/integration/test_steering.py -x -q`
- **Assigned To**: queue-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with task 2)
- Change queue key from project_key to chat_id using Redis List (`chat_queue:{chat_id}`)
- Update worker loop to pop ChatSessions from per-chat_id queues
- Wrap hot-path Popoto calls in `asyncio.to_thread()` (session creation, queue operations)
- Steering messages route to ChatSession via bounded Redis List
- Remove delete-and-recreate pattern — sessions transition to terminal states, never deleted
- On startup, drain old `queue:{project_key}` keys and re-enqueue into new `chat_queue:{chat_id}` keys (migration period)
- **Update tests**: test_job_queue_race.py, test_job_scheduler.py, test_steering.py

### 4. Implement ChatSession as orchestrator
- **Task ID**: build-chat-session-orchestrator
- **Depends On**: build-queue-rekey, build-registration-prototype
- **Validates**: `pytest tests/unit/test_observer.py tests/integration/test_agent_session_lifecycle.py tests/e2e/test_session_continuity.py -x -q`
- **Assigned To**: session-builder
- **Agent Type**: builder
- **Parallel**: false
- ChatSession spawns as Agent SDK session (read-only, PM persona)
- Absorbs Observer's role: deterministic decision table (see Observer Decision Table section)
- Uses DevSession registration mechanism validated in task 2
- Absorbs summarizer's delivery formatting into persona voice
- Classification happens once — routing.py lightweight classify, then ChatSession or simple session
- Simple Q&A messages bypass ChatSession (fast-path: session_type="simple", current behavior preserved)
- Remove old Observer (bridge/observer.py)
- Remove auto-continue caps (MAX_AUTO_CONTINUES, MAX_AUTO_CONTINUES_SDLC)
- **Update tests**: test_observer.py (rewrite for decision table), test_stop_reason_observer.py (replace), test_observer_early_return.py (delete), test_observer_message_for_user.py (delete), test_work_request_classifier.py, test_agent_session_lifecycle.py, test_session_continuity.py

### 5. Implement single-DevSession SDLC pipeline
- **Task ID**: build-single-dev-session
- **Depends On**: build-chat-session-orchestrator
- **Validates**: `pytest tests/unit/test_sdlc_env_vars.py tests/unit/test_sdk_client_sdlc.py tests/unit/test_pipeline_state_machine.py tests/unit/test_pipeline_integrity.py tests/e2e/test_message_pipeline.py -x -q`
- **Assigned To**: pipeline-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite SDLC prompt: full pipeline spec (via /sdlc skill) instead of "invoke /sdlc immediately" single-stage
- DevSession works through all stages in single process
- ChatSession monitors DevSession output, nudges between stages using decision table
- Remove re-enqueue loop and _enqueue_continuation
- Remove playlist concept
- **Concrete validation**: Run one real full-pipeline session on a non-trivial issue to validate context window behavior before shipping
- **Update tests**: test_auto_continue.py (delete), test_sdlc_playlist.py (delete), test_sdk_client_sdlc.py, test_sdlc_env_vars.py, test_pipeline_state_machine.py, test_pipeline_integrity.py, test_stage_aware_auto_continue.py (replace), test_enqueue_continuation.py (delete), test_message_pipeline.py (replace)

### 6. Cleanup and final test sweep
- **Task ID**: build-cleanup
- **Depends On**: build-single-dev-session
- **Validates**: `pytest tests/ -x -q`
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove double classification in sdk_client.py
- Remove any remaining dead code from old Observer, playlist, auto-continue
- Final test sweep: verify no remaining test files reference removed components
- Verify operational metrics instrumentation is in place (spawn counts, latency)

### 7. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-cleanup
- **Validates**: `pytest tests/ -x -q && python -m ruff check .`
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify ChatSession → DevSession relationship works
- Verify DevSession registration mechanism works end-to-end
- Verify per-chat_id queue serialization
- Verify full SDLC pipeline completes in single DevSession
- Verify simple Q&A messages handled without ChatSession overhead
- Verify steering messages route through ChatSession to active DevSession
- Verify no separate Observer component exists
- Verify operational metrics are being recorded

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/chat-dev-session-architecture.md`
- Update or remove `docs/features/observer-agent.md` (Observer absorbed into ChatSession)
- Update `CLAUDE.md` architecture diagram (update "Job" → "ChatSession/DevSession" terminology)
- Add entry to `docs/features/README.md`

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Validates**: `pytest tests/ -x -q && python -m ruff check . && python -m ruff format --check .`
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Verify bridge starts and processes a test message
- Verify operational metrics baseline is captured

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Model has discriminator | `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'session_type')"` | exit code 0 |
| Factory methods exist | `python -c "from models.agent_session import AgentSession; assert all(hasattr(AgentSession, m) for m in ['create_chat', 'create_dev', 'create_simple'])"` | exit code 0 |
| Dev agent defined | `grep -c 'dev-session' agent/agent_definitions.py` | 1+ |
| No budget refs | `grep -rn 'max_budget_usd\|budget_exceeded\|COST_WARN' agent/ bridge/ --include='*.py' \| grep -v types.py` | exit code 1 (no matches) |
| No double classify | `grep -c 'classify_work_request' agent/sdk_client.py` | 0 |
| No separate Observer | `test ! -f bridge/observer.py` | exit code 0 |
| Queue uses chat_id | `grep -q 'chat_queue' agent/job_queue.py` | exit code 0 |
| DevSession registration | `grep -q 'register_dev_session\|parent_chat_session_id' agent/sdk_client.py` | exit code 0 |
| Simple fast-path | `grep -q 'session_type.*simple' agent/sdk_client.py` | exit code 0 |

## Migration Strategy

### In-Flight Session Handling
On deploy, restart the bridge. Any in-flight sessions are abandoned (existing crash recovery handles this). Old AgentSession records in Redis are harmless — they use different key patterns and won't collide with new ChatSession/DevSession records. No data migration needed.

### Queue Cutover
Old queue keys (`queue:{project_key}`) are drained on new bridge startup. The new code checks for old-format queue keys and re-enqueues any remaining items into `chat_queue:{chat_id}` format. After one successful restart cycle, old keys will be empty. Telethon's persistent session with `pts` tracking ensures messages arriving during the ~2s restart window are fetched on reconnect — no message loss.

### Rollback Path
If bugs surface after Phase 1 (model split): revert the commit, restart bridge. Old AgentSession code paths still work because the model file is restored. New ChatSession/DevSession records in Redis are orphaned but harmless (TTL cleanup). Each phase is independently revertable via git revert + restart.

## RFC Feedback

| Severity | Critic | Feedback | Plan Response |
|----------|--------|----------|---------------|
| CONCERN | code-reviewer | Deterministic observer loses ability to handle novel failure modes | Added: Observer Decision Table with explicit ambiguity rule — if table doesn't match, deliver to human. No LLM fallback. |
| CONCERN | code-reviewer | Single-session serializes stages, roughly doubling wall-clock time | Acknowledged tradeoff. Offset by eliminating 7x spawn overhead. Sub-agents parallelize heavy subtasks. Operational metrics track actual impact. |
| CONCERN | code-reviewer | Phase 1 (model split) ships before Observer rewrite, so new models exercised only by compat shim | Accepted risk. Model split is low-risk (additive). Observer rewrite in Phase 4 validates the models. |
| CONCERN | async-specialist | Single asyncio.Lock contention on active_sessions dict | Addressed: use per-chat_id locks via `defaultdict(asyncio.Lock)` with brief global lock for insertion only. |
| CONCERN | async-specialist | Observer re-invocation as steering vs new queue entry underspecified | Addressed in Session Creation Contract: ChatSession steers via bounded Redis List, never spawns new sessions. |
| CONCERN | async-specialist | Popoto ORM uses synchronous Redis calls blocking event loop | Addressed: wrap hot-path Popoto calls in `asyncio.to_thread()`. Full async migration is out of scope. |
| CONCERN | async-specialist | Graceful shutdown of long-running sessions on bridge restart | Addressed in Session Liveness: configurable max lifetime per session type. On restart, sessions are abandoned and can resume via claude_session_uuid. |
| CONCERN | async-specialist | Memory growth from long Claude sessions | Claude Code handles context compression internally. Sub-agents for heavy tasks keep main context clean. Operational metrics track context exhaustion incidents. |
| CONCERN | data-architect | job_type must be authoritative, never re-derived | Addressed: session_type is set once at creation via factory method. SDLC is a DevSession property (sdlc_stages != null). |
| CONCERN | data-architect | No session sequence numbering for "current session" lookup | Addressed: ChatSession queries DevSessions by parent_chat_session_id; latest by created_at. |
| CONCERN | data-architect | No concurrent-dequeue protection specified | Addressed: atomic `RPOP` on Redis List guarantees exactly-once dequeue. |
| BLOCKER | war-room/Skeptic | DevSession registration in Redis unspecified | Added: spike-4, Phase 2 (prototype), and detailed mechanism in "How ChatSession Spawns DevSession" section. |
| CONCERN | war-room/Skeptic+Simplifier | ChatSession adds spawn overhead to every message | Added: simple session fast-path. Q&A/non-SDLC messages bypass ChatSession entirely. |
| CONCERN | war-room/Skeptic+Operator | Context window exhaustion hand-waved | Added: concrete validation step in Phase 5 — run real full-pipeline before shipping. |
| CONCERN | war-room/Archaeologist+Operator | Deterministic Observer unspecified | Added: Observer Decision Table with all (stop_reason, session_state, sdlc_stage) tuples. |
| CONCERN | war-room/Operator+Simplifier | ~600 tests serialized in single task | Restructured: tests updated per-phase, each task lists its specific test files. |
| CONCERN | war-room/Adversary | Queue cutover race window | Added: Race 5 with Telethon pts tracking mitigation and old-key drain on startup. |
| CONCERN | war-room/Adversary+Operator | Popoto sync calls worsen with more Redis ops | Added: asyncio.to_thread() wrapping for hot-path calls in Phase 3. |
| NIT | war-room/Archaeologist | Issue #459 uses "Job" terminology vs plan's "ChatSession" | Noted: update issue after plan is finalized. Documentation task includes CLAUDE.md terminology update. |

## Resolved Questions

1. **Keep /sdlc skill.** It remains the ground truth for the Observer (alongside the pipeline graph). Used manually in Claude Code sessions and by the Observer to steer sessions. Rewritten as full pipeline spec, not single-stage router.

2. **Remove "playlist" concept entirely.** Messages start and end with ChatSessions or simple sessions — no remaining connection between sessions via playlist queues. If an agent needs to send a Telegram message (e.g., to queue the next issue), it uses the Telegram skill like any other tool. The playlist feature (#450) is deprecated by this redesign.

3. **Summarizer merges with persona message writing.** The summarizer's formatting role is absorbed into the persona's message-writing capability. Each persona (Dev, PM) has its own voice for composing delivery messages. The summarizer as a separate component is deprecated.

4. **Chat messages always spawn new ChatSessions (for SDLC) or simple sessions (for Q&A).** Every new SDLC message creates a new ChatSession. Simple Q&A messages create simple sessions with no orchestration overhead. The ~7-second window after a ChatSession starts allows rapid follow-up messages to become steering automatically (existing behavior). Reply-to messages are always steering for the referenced ChatSession.

5. **Slugs are agent-created, not user-specified.** A message cannot "arrive for an existing slug" — the DevSession agent writes the slug during execution. Reply-to messages are steering for the parent ChatSession, not slug-based routing.

6. **Budget removal is a prerequisite, not a task.** The budget system is already removed on the `session/remove_budget_system` branch (#458). Merge that branch before starting this work.

7. **DevSession registration is validated early.** Phase 2 prototypes and validates the registration mechanism before any dependent work begins. If neither SDK events nor MCP tools work, the design can be adjusted before significant investment.
