---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-03-10
tracking: https://github.com/valorengels/ai/issues/334
---

# Correlation IDs for End-to-End Request Tracing

## Problem

There is no single identifier tying a message's journey through the system together. Each component logs independently with different identifiers (msg_id, job_id, session_id, request_id), making it difficult to trace a request from Telegram receipt through job queue, worker, SDK client, observer, and back to response delivery.

**Current behavior:**
Debugging requires timestamp matching across `bridge.log` lines. Under load, interleaved log lines from concurrent sessions make this error-prone and slow.

**Desired outcome:**
Every log line for a single message journey includes a shared `[correlation_id]` prefix, enabling instant grep-based tracing from receipt to response.

## Prior Art

No prior issues found related to this work. The SDK client already generates a `request_id` (`f"{session_id}_{int(start_time)}"` at line 894 of `agent/sdk_client.py`) but this is created too late and not shared upstream.

## Data Flow

1. **Entry point**: `bridge/telegram_bridge.py` handler() receives Telegram message (line 522)
2. **Bridge processing**: message stored, should_respond checked, session_id computed, reaction set
3. **Enqueue**: `agent/job_queue.py` enqueue_job() creates AgentSession in Redis (line 768)
4. **Worker pickup**: `_execute_job()` dequeues job, saves session snapshot, starts agent (line 1015)
5. **SDK client**: `agent/sdk_client.py` get_agent_response_sdk() generates request_id, enriches message, invokes Claude (line 860)
6. **Agent work**: Claude Code subprocess runs, produces output
7. **Observer**: `bridge/observer.py` Observer class reads session state, decides steer vs deliver (line 194)
8. **Transcript**: `bridge/session_transcript.py` logs turns to transcript file
9. **Snapshot**: `bridge/session_logs.py` saves session snapshots at lifecycle transitions
10. **Response**: output sent to Telegram or re-enqueued for continuation

## Architectural Impact

- **New dependencies**: None (UUID is stdlib)
- **Interface changes**: `correlation_id` parameter added to `enqueue_job()`, `_push_job()`, `get_agent_response_sdk()`, `Observer.__init__()`, `start_transcript()`, `save_session_snapshot()`; `AgentSession` model gets a new `correlation_id` field
- **Coupling**: Minimal increase — correlation_id is a pass-through string, not a behavioral dependency
- **Data ownership**: Bridge generates the ID, all downstream components receive it
- **Reversibility**: Easy — remove the parameter and field; logging reverts to current behavior

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a threading-through-parameters change touching 6+ files. Each file change is small but the breadth requires care to avoid missing a handoff point.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Correlation ID generator**: `uuid.uuid4().hex[:12]` at message receipt in `handler()` — short enough for log readability, unique enough for tracing
- **AgentSession field**: New `correlation_id` field on the model, set at enqueue time
- **Log prefix convention**: All log lines include `[{correlation_id}]` for grep-ability

### Flow

**Telegram message received** → generate correlation_id → **enqueue_job** (stored in AgentSession) → **_execute_job** (read from job, pass to SDK client) → **get_agent_response_sdk** (replaces request_id, used in all logging) → **Observer** (included in decision logging) → **transcript/snapshot** (included in headers/metadata) → **response delivered**

### Technical Approach

- Generate `correlation_id = uuid.uuid4().hex[:12]` in `handler()` immediately after deciding to respond
- Add `correlation_id: str | None = None` parameter to `enqueue_job()` and `_push_job()`
- Add `correlation_id = Field(null=True)` to `AgentSession` model
- In `_execute_job()`, read `job.correlation_id` and pass to `get_agent_response_sdk()`
- In `get_agent_response_sdk()`, use `correlation_id` as the log prefix instead of the internally-generated `request_id`
- In `Observer.__init__()`, accept and log `correlation_id`
- In `start_transcript()` and `save_session_snapshot()`, include `correlation_id` in headers/metadata
- For auto-continue jobs, the continuation inherits the parent's `correlation_id`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new exception handlers introduced — correlation_id is purely additive logging
- [ ] Existing `except Exception` blocks in touched files already have logging; correlation_id just enriches the log prefix

### Empty/Invalid Input Handling
- [ ] If `correlation_id` is None (e.g., direct SDK client usage without bridge), fall back to generating one locally in `get_agent_response_sdk()`
- [ ] Empty string correlation_id treated same as None

### Error State Rendering
- [ ] No user-visible output changes — correlation_id is internal infrastructure

## Rabbit Holes

- **Structured/JSON logging**: Tempting to refactor all logging to JSON format while adding correlation IDs, but this is a separate concern with much larger blast radius
- **OpenTelemetry integration**: Distributed tracing is overkill for a single-process Python bridge
- **Request ID deprecation**: The existing `request_id` in sdk_client.py could be removed, but keeping it as an alias avoids breaking any external log parsers

## Risks

### Risk 1: Missing a propagation point
**Impact:** Some log lines lack the correlation_id, defeating the purpose
**Mitigation:** Search for all `logger.info/warning/error` calls in touched files and verify each includes the ID. Validation step in plan.

### Risk 2: AgentSession model migration
**Impact:** Adding a field to a Popoto model could cause issues with existing Redis data
**Mitigation:** Popoto fields with `null=True` are safe to add — existing records simply have None for the new field.

## Race Conditions

No race conditions identified. The correlation_id is generated once, immutably, at message receipt and passed through as a read-only string. No concurrent writers.

## No-Gos (Out of Scope)

- Structured/JSON logging format (separate project)
- OpenTelemetry or external tracing integration
- Log aggregation tooling
- Changing the session_id format or semantics
- Adding correlation_id to Telegram messages or user-visible output

## Update System

No update system changes required — this feature is purely internal logging infrastructure. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a bridge-internal change. The correlation_id is infrastructure for debugging, not a tool or capability exposed to the agent.

## Documentation

- [ ] Create `docs/features/correlation-ids.md` describing the tracing capability and how to use it for debugging
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Code comments on the correlation_id generation and propagation pattern

## Success Criteria

- [ ] correlation_id generated at message receipt in `bridge/telegram_bridge.py`
- [ ] correlation_id stored in AgentSession model via `agent/job_queue.py`
- [ ] correlation_id used as log prefix in `agent/sdk_client.py` (replacing internally-generated request_id)
- [ ] correlation_id logged in `bridge/observer.py` decision output
- [ ] correlation_id included in transcript headers via `bridge/session_transcript.py`
- [ ] correlation_id included in snapshot metadata via `bridge/session_logs.py`
- [ ] Auto-continue jobs inherit parent's correlation_id
- [ ] All log lines in a single message journey are greppable by correlation_id
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (correlation-threading)**
  - Name: correlation-builder
  - Role: Thread correlation_id through all 6 files
  - Agent Type: builder
  - Resume: true

- **Validator (correlation-verify)**
  - Name: correlation-validator
  - Role: Verify all log lines include correlation_id and grep works end-to-end
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add correlation_id field to AgentSession
- **Task ID**: build-model
- **Depends On**: none
- **Assigned To**: correlation-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `correlation_id = Field(null=True)` to `models/agent_session.py`

### 2. Generate and propagate through bridge and job queue
- **Task ID**: build-bridge-queue
- **Depends On**: build-model
- **Assigned To**: correlation-builder
- **Agent Type**: builder
- **Parallel**: false
- Generate `correlation_id` in `bridge/telegram_bridge.py` handler()
- Add parameter to `enqueue_job()` and `_push_job()` in `agent/job_queue.py`
- Pass through to AgentSession.async_create()
- Include in enqueue/dequeue/execute log lines

### 3. Propagate to SDK client
- **Task ID**: build-sdk
- **Depends On**: build-bridge-queue
- **Assigned To**: correlation-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `correlation_id` parameter to `get_agent_response_sdk()`
- Replace or augment the internally-generated `request_id` with correlation_id
- Update all log lines in the function to use correlation_id prefix

### 4. Propagate to observer
- **Task ID**: build-observer
- **Depends On**: build-bridge-queue
- **Assigned To**: correlation-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-sdk)
- Add `correlation_id` to Observer.__init__()
- Include in all observer tool-use iteration and decision log lines

### 5. Include in transcripts and snapshots
- **Task ID**: build-transcripts
- **Depends On**: build-bridge-queue
- **Assigned To**: correlation-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-sdk, build-observer)
- Add `correlation_id` parameter to `start_transcript()` and include in transcript file header
- Add `correlation_id` to `save_session_snapshot()` extra_context

### 6. Validate all propagation points
- **Task ID**: validate-all
- **Depends On**: build-sdk, build-observer, build-transcripts
- **Assigned To**: correlation-validator
- **Agent Type**: validator
- **Parallel**: false
- Grep all touched files for logger calls, verify correlation_id presence
- Verify AgentSession field exists
- Verify auto-continue inherits correlation_id
- Run `python -m ruff check . && python -m ruff format --check .`

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: correlation-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/correlation-ids.md`
- Add entry to `docs/features/README.md` index table

## Validation Commands

- `grep -rn "correlation_id" bridge/telegram_bridge.py agent/job_queue.py agent/sdk_client.py bridge/observer.py bridge/session_transcript.py bridge/session_logs.py models/agent_session.py` - Verify presence in all files
- `python -c "from models.agent_session import AgentSession; print('correlation_id' in [f for f in dir(AgentSession) if not f.startswith('_')])"` - Verify model field
- `python -m ruff check .` - Lint passes
- `python -m ruff format --check .` - Format passes
- `pytest tests/ -x -q` - Tests pass

---

## Open Questions

None — the scope is well-defined by issue #334 and the implementation is straightforward parameter threading.
