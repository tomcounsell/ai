---
status: Ready
type: feature
appetite: Large
owner: Valor Engels
created: 2026-03-09
tracking: https://github.com/tomcounsell/ai/issues/309
---

# Observer Agent: Stage-Aware SDLC Steerer

## Problem

SDLC stage progress and pipeline completion have been broken across 20+ PRs. Each fix addressed a downstream symptom while the root cause persists: three interleaved systems (classifier in `summarizer.py`, routing logic in `job_queue.py`, coaching in `coach.py`) share responsibility for deciding what happens when the worker agent stops. None has full context.

**Current behavior:**
1. Stages get skipped — patch and docs stages are frequently omitted
2. Pipeline stalls — agent stops mid-pipeline, auto-continue doesn't have enough context to steer
3. Pipeline escapes — agent commits directly to main because nothing enforces branch creation
4. Links go missing — issue URLs, PR URLs, plan paths aren't reliably tracked because the LLM must remember to call `session_progress`
5. Silent failures — `except Exception: pass` blocks around stage writes silently swallow errors

**Desired outcome:**
A single Observer Agent replaces the fragmented decision system. Stage detection is deterministic (not LLM-dependent). The Observer has full context via AgentSession and makes judgment calls about steering vs delivering. Pipeline progression is reliable and auditable.

## Prior Art

This is the culmination of 20+ issues attempting to fix the same root cause:

- **#106, #124, #177**: Early summarizer format fixes, coaching loop — no stage data existed yet
- **#178, #186, #198, #202**: Wire session_progress, stage-aware routing — LLM-dependent CLI calls silently failed
- **#225, #227, #240, #241, #243**: SDLC-first routing, template rendering — stage data still not being written reliably
- **#274, #276, #278, #280, #285**: Structured summarizer, classifier fixes — addressed rendering/classification, not data flow
- **#293, #294, #296, #298**: Test gaps, pipeline stalls, silent completion — each fixed one symptom, others remained

## Data Flow

### Current Flow (fragmented)
1. **Worker agent stops** → `send_to_chat()` closure fires in `job_queue.py:1244`
2. **Re-read session** → Fresh AgentSession from Redis for stage data
3. **Stage-aware check** → `is_sdlc_job()`, `has_remaining_stages()`, `has_failed_stage()`
4. **Classifier** → `classify_output()` in `summarizer.py` (Haiku LLM or heuristics)
5. **Routing decision** → `classify_routing_decision()` in `job_queue.py:107`
6. **If auto-continue** → `_enqueue_continuation()` builds coaching via `coach.py`, re-enqueues job
7. **If deliver** → `send_cb()` sends to Telegram via summarizer rendering

**Problems in this flow:** Stage data depends on the worker LLM calling `session_progress.py` CLI. Classifier + coach + routing logic each make partial decisions without seeing the full picture. Coaching tiers in `coach.py` are ordered by priority but lack the context to make nuanced judgments.

### Proposed Flow (Observer)
1. **Worker agent stops** → `send_to_chat()` fires
2. **Deterministic stage detection** → Parse worker transcript for `/do-plan`, `/do-build` etc. invocations, update AgentSession stages
3. **Observer Agent (Sonnet)** → Runs synchronously with full AgentSession context
   - Reads session (stages, links, history, queued messages)
   - Extracts artifacts from worker output
   - Decides: steer to next stage OR deliver to Telegram
4. **Action dispatch** → Either enqueue continuation or send to Telegram

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #185 (stage-aware auto-continue) | Added stage progress as routing signal | Still relies on LLM writing stage entries via CLI |
| PR #126 (coaching loop) | Context-aware coaching messages | Coach has no access to session state; generates from classification alone |
| PR #284 (classifier type fix) | Fixed classifier not outputting "sdlc" | Addressed one symptom; stage data still unreliable |
| PR #286 (session as source of truth) | Stopped creating duplicate sessions | Fixed data model but not the decision-making fragmentation |
| PR #300 (open questions gate) | Pause pipeline for plan questions | Point fix; other pause conditions still broken |

**Root cause pattern:** Every fix improves one component's partial view without addressing the fundamental problem: three systems with partial context making decisions that require full context. The fix is architectural — consolidate decision-making into a single agent with full context.

## Architectural Impact

- **New dependencies**: Claude API (Sonnet) for Observer — already available via existing `anthropic` SDK
- **Interface changes**: `send_to_chat()` routing logic replaced by Observer dispatch; `_enqueue_continuation()` reused by Observer
- **Coupling**: Significantly **decreases** coupling — replaces 3 tightly-coupled systems (classifier→coach→routing) with 1 unified decision-maker
- **Data ownership**: AgentSession remains the single source of truth; Observer reads and writes it atomically
- **Reversibility**: Git revert if needed — old system is broken anyway, no value preserving it

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (architecture validation, initial rollout review)
- Review rounds: 2+ (architecture review, post-rollout review)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ANTHROPIC_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('ANTHROPIC_API_KEY')"` | Sonnet API access for Observer |
| Redis running | `python -c "import redis; redis.Redis().ping()"` | AgentSession storage |

Run all checks: `python scripts/check_prerequisites.py docs/plans/observer_agent.md`

## Solution

### Key Elements

- **Stage Detector**: Deterministic transcript parser that marks stages complete without LLM involvement
- **Observer Agent**: Sonnet-powered agent with tools that reads session state, makes judgment calls about steering vs delivering, and updates session state
- **Interjection Queue**: `queued_steering_messages` field on AgentSession for buffering human replies during active pipelines (populated by bridge intake classifier, #320)

### Flow

**Worker stops** → Stage detector parses transcript → Observer reads session → Observer decides (steer/deliver) → Observer updates session → Action dispatched

### Technical Approach

- **Observer runs synchronously inside `send_to_chat()`** — replaces the classifier→coach→routing chain at the same call site. No new process, no new hook mechanism. The Observer is called where `classify_output()` + `classify_routing_decision()` + `build_coaching_message()` are called today.
- **Stage detector is a pure function** — takes transcript text, returns list of completed stages. Called before Observer so it always sees current state.
- **Observer uses Claude API directly** — system prompt + tools, invoked via `anthropic.Anthropic().messages.create()` with tool_use. Not a Claude Code subprocess.
- **Direct replacement** — Observer replaces the classifier→coach→routing chain entirely. No feature flag, no shadow mode. The existing system is the problem, not a useful baseline. Git revert is the rollback if needed.
- **Reuse existing infrastructure** — `_enqueue_continuation()` already handles job re-enqueueing; Observer calls it via its `enqueue_continuation` tool. `send_cb()` already handles Telegram delivery; Observer calls it via `deliver_to_telegram` tool.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit all `except Exception: pass` in `agent_session.py` — `append_history()` line 140 and `set_link()` line 161 both silently swallow save errors. Observer path must log these.
- [ ] Observer agent errors must not crash the pipeline — fallback delivers raw output to Telegram

### Empty/Invalid Input Handling
- [ ] Stage detector given empty transcript → returns empty list (no stages marked)
- [ ] Observer given session with no history → handles gracefully, defaults to first stage
- [ ] Empty worker output → Observer delivers "empty output" message rather than auto-continuing

### Error State Rendering
- [ ] Observer's `deliver_to_telegram` surfaces error context when worker hit a blocker
- [ ] Failed Observer invocation sends the raw worker output to Telegram (never silently drops)

## Rabbit Holes

- **Making Observer a separate process/service** — adds complexity for no benefit. It runs in-process, synchronously, inside `send_to_chat()`. The latency cost of one Sonnet call is acceptable.
- **Rewriting the summarizer's artifact extraction** — `extract_artifacts()` works fine. Observer uses it as a utility, not as a decision-maker.
- **Adding more stages to the pipeline** — the stage order is correct; the problem is reliability of progression, not stage design.
- **Building a custom tool-use framework** — use the `anthropic` SDK's native tool_use support directly. No abstraction layer needed.
- **Optimizing Observer latency** — premature. Measure in production first. If needed, switch to Haiku later (but judgment quality matters more than speed here).

## Risks

### Risk 1: Observer makes worse decisions than the current system
**Impact:** Pipeline stalls more often or delivers at wrong times
**Mitigation:** Integration tests with real session snapshots validate decisions before rollout. The current system is broken across 20+ issues — the bar to clear is low. Git revert is the rollback.

### Risk 2: Sonnet API latency adds noticeable delay
**Impact:** User sees delayed responses after each worker pause
**Mitigation:** Observer runs at a natural pause point (worker already stopped). User doesn't see the delay because the next action (steer or deliver) happens after. Measure actual latency in production logs.

### Risk 3: Observer tool-use loop doesn't converge
**Impact:** Observer calls tools indefinitely, blocking the pipeline
**Mitigation:** Set `max_tokens` limit on Observer call. Require `read_session` first and `update_session` last in system prompt. Cap tool-use iterations (e.g., max 5 tool calls).

## Race Conditions

### Race 1: Concurrent session updates during Observer execution
**Location:** `bridge/observer.py` — between `read_session` and `update_session`
**Trigger:** Human sends a reply while Observer is running, bridge writes to `queued_steering_messages`
**Data prerequisite:** AgentSession must exist in Redis before Observer reads it
**State prerequisite:** Observer must see the latest `queued_steering_messages`
**Mitigation:** Observer re-reads session in `update_session` before writing. Merge strategy: append-only for history, union for queued messages. The bridge only appends to `queued_steering_messages`; Observer only clears it. No conflicting writes.

### Race 2: Stage detector and session_progress.py writing simultaneously
**Location:** Stage detector in `send_to_chat()` vs `session_progress.py` CLI in worker subprocess
**Trigger:** Worker invokes `/do-build` (detected by stage detector) while also calling `session_progress --stage BUILD`
**Data prerequisite:** History entries must be consistent
**State prerequisite:** Both writers must see each other's entries
**Mitigation:** `session_progress.py` CLI is deleted as part of this work. Stage detector is the sole writer. No dual-write window.

## No-Gos (Out of Scope)

- **Expectations-based message routing** (#318) — Observer sets `expectations` and `context_summary` but routing changes are follow-up work
- **Logging and telemetry system** (#319) — Observer logs decisions but dedicated telemetry is separate
- **Calendar hook changes** — it works independently and is not modified
- **Changing the SDLC stage order** — the pipeline sequence is correct
- **Worker agent prompt changes** — don't add more instructions to the worker about stage recording; that IS the problem

## Update System

No update system changes required. Remote machines get the Observer code via `git pull`. No new dependencies beyond the already-installed `anthropic` SDK. No new config — Observer replaces the old routing directly.

## Agent Integration

No agent integration required — this is a bridge-internal change. The Observer runs inside the bridge's `send_to_chat()` path. It uses the Claude API directly (not MCP tools). No changes to `.mcp.json` or `mcp_servers/`.

The bridge (`bridge/telegram_bridge.py`) already calls `send_to_chat()` via the job queue — no new imports needed in the bridge itself.

## Documentation

### Feature Documentation
- [x] Create `docs/features/observer-agent.md` describing the Observer architecture and decision flow
- [x] Add entry to `docs/features/README.md` index table
- [x] Update `docs/features/coaching-loop.md` to note deprecation of coaching tiers (replaced by Observer)

### Inline Documentation
- [x] Code comments on Observer system prompt and tool definitions
- [x] Docstrings for stage detector pure function

## Success Criteria

- [ ] Stage progress updates on EVERY worker stop (deterministic detector, not LLM-dependent)
- [ ] Observer correctly steers through full SDLC pipeline in integration test
- [ ] Missing links detected and worker instructed to create them
- [ ] Observer reads `queued_steering_messages` when present (populated by #320)
- [ ] Thumbs-up reaction still completes session without Observer involvement
- [ ] Calendar hook continues working unchanged alongside Observer
- [ ] Old routing code (`classify_output`, `classify_routing_decision`, `build_coaching_message`) removed from routing path
- [ ] Zero silent failures in stage recording path (no `except: pass` on stage writes)
- [ ] Observer sets `expectations` and `context_summary` on deliver (ready for #318)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (stage-detector)**
  - Name: stage-detector-builder
  - Role: Implement deterministic stage detector as pure function
  - Agent Type: builder
  - Resume: true

- **Builder (observer-core)**
  - Name: observer-builder
  - Role: Implement Observer agent with tools, system prompt, and API invocation
  - Agent Type: builder
  - Resume: true

- **Builder (integration)**
  - Name: integration-builder
  - Role: Wire Observer into send_to_chat(), replace old routing, add queued_steering_messages field
  - Agent Type: builder
  - Resume: true

- **Validator (architecture)**
  - Name: architecture-validator
  - Role: Verify Observer replaces all three systems correctly, no orphaned code paths
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: observer-tester
  - Role: Integration tests with real session snapshots
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: observer-docs
  - Role: Create feature documentation and update index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Implement Stage Detector
- **Task ID**: build-stage-detector
- **Depends On**: none
- **Assigned To**: stage-detector-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `bridge/stage_detector.py` with pure function `detect_stages(transcript: str) -> list[str]`
- Parse for `/do-plan`, `/do-build`, `/do-test`, `/do-pr-review`, `/do-docs` invocations in transcript
- Map detected invocations to stage completions: running a skill means the previous stage is done
- Return list of stage names to mark as completed
- Unit tests: given transcript snippets, assert correct stages returned

### 2. Add queued_steering_messages to AgentSession
- **Task ID**: build-session-field
- **Depends On**: none
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `queued_steering_messages = ListField(null=True)` to `models/agent_session.py`
- Add helper methods: `push_steering_message(text)`, `pop_steering_messages() -> list[str]`
- Unit tests for push/pop/clear lifecycle

### 3. Implement Observer Agent
- **Task ID**: build-observer
- **Depends On**: build-stage-detector, build-session-field
- **Assigned To**: observer-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `bridge/observer.py` with Observer class
- Define system prompt (SDLC pipeline definition, decision guidelines)
- Define tools: `read_session`, `update_session`, `enqueue_continuation`, `deliver_to_telegram`
- Implement tool dispatch: each tool calls existing infrastructure (`AgentSession` methods, `_enqueue_continuation()`, `send_cb()`)
- Observer decision and reasoning are short-lived in-memory — not persisted to database. The only durable output is the steering message sent back to the worker (via `enqueue_continuation`) or the summary delivered to Telegram.
- Invoke via `anthropic.Anthropic().messages.create()` with Sonnet, tool_use, max 5 tool iterations
- Add fallback: if Observer errors, log the error and deliver raw worker output to Telegram (never silently drop)

### 4. Wire Observer into send_to_chat()
- **Task ID**: build-wiring
- **Depends On**: build-observer
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- In `job_queue.py:send_to_chat()`, replace the classifier→coach→routing chain with: stage detector → Observer
- Delete `classify_output()` call, `classify_routing_decision()` call, and `build_coaching_message()` call from the routing path
- Pass Observer the worker output, session, send_cb, and enqueue function references
- Keep the early guards (completed session check, completion_sent check, empty output guard)

### 5. Validate Architecture
- **Task ID**: validate-architecture
- **Depends On**: build-wiring
- **Assigned To**: architecture-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify Observer replaces: `classify_output()` decisions, `classify_routing_decision()`, `build_coaching_message()`
- Verify old routing code (`classify_output`, `classify_routing_decision`, `build_coaching_message`) is fully removed from the routing path
- Verify no orphaned imports or dead code
- Verify `session_progress.py` CLI is deleted or converted to a thin wrapper around stage detector

### 6. Integration Tests
- **Task ID**: test-observer
- **Depends On**: validate-architecture
- **Assigned To**: observer-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Test Observer with real AgentSession snapshots from production
- Test stage detector with real transcripts
- Test human interjection flow: push message → Observer reads → incorporates
- Test link accountability: Observer detects missing PR link after BUILD stage
- Test fallback: Observer API error → raw output delivered to Telegram (never silently dropped)

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: test-observer
- **Assigned To**: observer-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/observer-agent.md`
- Add entry to `docs/features/README.md` index table
- Update `docs/features/coaching-loop.md` deprecation note

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: architecture-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Validation Commands

- `python -m pytest tests/ -x -q` - Run all tests
- `python -m ruff check .` - Lint check
- `python -m ruff format --check .` - Format check
- `grep -rL "classify_output\|classify_routing_decision\|build_coaching_message" agent/job_queue.py` - Verify old routing removed
- `python -c "from bridge.observer import Observer; print('Observer importable')"` - Verify module exists
- `python -c "from bridge.stage_detector import detect_stages; print('Stage detector importable')"` - Verify module exists
- `python -c "from models.agent_session import AgentSession; assert hasattr(AgentSession, 'queued_steering_messages')"` - Verify field exists

---

## Resolved Questions

1. **Observer model**: Sonnet from the start. Owner will switch to Haiku later if latency matters.

2. **Persistence**: Observer decisions are short-lived in memory. No database writes for classification/coaching. Only durable outputs: steering message to worker or summary to Telegram. Structured logging is follow-up work (#319).

3. **Interjection source**: Bridge intake classifier (#320) will classify incoming messages and populate `queued_steering_messages` on active sessions. This plan assumes that feature exists — Observer reads the field but doesn't own populating it.
