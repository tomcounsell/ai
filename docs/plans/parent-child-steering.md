---
status: Complete
type: feature
appetite: Small
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/491
last_comment_id:
---

# PM session Steering: Parent-to-Child Dev session Steering

## Problem

When a PM session (PM persona) spawns a Dev session via the Agent tool, it loses the ability to influence that session until it completes. The only way to steer a running Dev session today is via a Telegram reply message, which routes through the bridge. The PM session -- the orchestrator that spawned the work -- has no mechanism to inject steering messages into its children.

**Current behavior:**
PM session spawns a Dev session for a BUILD stage. Mid-execution, the PM realizes the dev should skip docs and focus on tests. There is no mechanism to communicate this -- the PM must wait for the Dev session to complete and then re-dispatch, wasting compute.

**Desired outcome:**
PM session can push steering messages to any active child Dev session at any time. This is explicit and opt-in -- the PM decides when and what to steer. The message appears in the child's context at the next tool call via the existing watchdog/steering infrastructure.

## Prior Art

- **Issue #292 / PR #308**: Fixed reply-to steering reaching running agents. Established the bridge-to-steering-queue routing pattern. Directly relevant -- this work extends the same push mechanism to a new caller (PM session instead of bridge).
- **Issue #329 / PR #349**: Context fidelity modes for sub-agent steering. Added fidelity levels to steering payloads. Relevant -- the new tool should respect existing fidelity conventions.
- **Issue #318 / PR #366**: Route unthreaded messages into active sessions via expectations + queued_steering_messages. Extended steering routing beyond reply-to threads.
- **Issue #459 / PR #464**: SDLC Redesign introducing PM/Dev session split. Created the parent-child relationship (`parent_chat_session_id`) that this feature relies on.
- **Issue #23**: Original adoption of steering concepts from pi-mono. Foundation for the entire steering queue architecture.

## Data Flow

1. **Entry point**: PM session (running in Agent SDK) decides to steer a child Dev session
2. **Tool invocation**: PM session calls a bash script (`scripts/steer_child.py`) with the child session ID and message text
3. **Validation**: Script validates the target is an active child of the calling session by checking `parent_chat_session_id` in AgentSession model
4. **Push**: Script calls `push_steering_message(child_session_id, text, sender="PM session")` writing to Redis key `steering:{child_session_id}`
5. **Consumption**: On the child Dev session's next tool call, the watchdog hook (`_handle_steering` in `health_check.py`) pops the message from Redis
6. **Injection**: Watchdog calls `client.interrupt()` + `client.query()` on the child's SDK client to inject the steering message into the Dev session's context
7. **Output**: Dev session sees the steering message and adjusts its behavior accordingly

## Architectural Impact

- **New dependencies**: None -- uses existing `agent/steering.py`, `models/agent_session.py`, and Redis
- **Interface changes**: One new callable script. No changes to existing function signatures
- **Coupling**: Minimal increase -- the script imports from existing modules (`agent.steering`, `models.agent_session`)
- **Data ownership**: No change -- steering queue ownership remains with the session that consumes it
- **Reversibility**: Trivial -- removing the script has zero impact on existing functionality

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (well-specified issue, clear acceptance criteria)
- Review rounds: 1 (standard code review)

Solo dev work is fast -- the bottleneck is alignment and review. The plumbing already exists; this is wiring a new caller to existing infrastructure.

## Prerequisites

No prerequisites -- this work has no external dependencies. All required infrastructure (Redis, steering queue, AgentSession model, watchdog hook) is already in place.

## Solution

### Key Elements

- **`scripts/steer_child.py`**: A callable Python script that PM session invokes via bash to push steering messages to child Dev sessions
- **Parent-child validation**: Ensures the target session is an active child of the calling session, preventing cross-session steering
- **Abort support**: The script accepts an `--abort` flag to send hard-stop signals to children

### Flow

**PM session decides to steer** -> Calls `python scripts/steer_child.py --session-id <child_id> --message "focus on tests"` -> Script validates parent-child relationship -> `push_steering_message()` writes to Redis -> Child's watchdog picks up on next tool call -> Dev session adjusts behavior

### Technical Approach

- **Bash-callable script** (not MCP tool): PM session already has bash access via the Agent tool. A script is zero-overhead -- no MCP server registration, no context pollution from tool descriptions, and trivially testable. The PM session can call it like any other bash command.
- **Validation via `parent_chat_session_id`**: The script takes the calling session's ID (from environment variable `CLAUDE_SESSION_ID` or explicit argument) and verifies the target is a child by checking `AgentSession.parent_chat_session_id == caller_id`. This prevents a PM session from steering arbitrary sessions.
- **Abort support via `--abort` flag**: Maps directly to `push_steering_message(..., is_abort=True)`. The existing watchdog abort handling in `_handle_steering` takes care of the rest.
- **Session discovery via `--list` flag**: PM session can list its active children before steering, using `AgentSession.get_dev_sessions()` filtered to `status="running"`.

The script needs the parent session ID to validate ownership. Two options:
1. Pass it explicitly via `--parent-id` argument
2. Read it from `CLAUDE_SESSION_ID` environment variable (already set by `sdk_client.py` for Dev sessions)

Option 1 is more explicit and reliable. The PM session knows its own session ID and can pass it.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Script must exit with non-zero code and print error message for: invalid child ID, non-child target, inactive session
- [ ] `push_steering_message` already handles Redis errors (logs warning) -- no new exception paths needed

### Empty/Invalid Input Handling
- [ ] Empty message text: script rejects with "message cannot be empty"
- [ ] Empty session ID: script rejects with "session-id is required"
- [ ] Non-existent session ID: script prints "session not found" and exits 1
- [ ] Whitespace-only message: script strips and rejects if empty

### Error State Rendering
- [ ] All error paths print to stderr and exit with non-zero code
- [ ] Success path prints confirmation to stdout (e.g., "Steered dev-xxx: <message preview>")

## Test Impact

No existing tests affected -- this is a greenfield feature adding a new script and new test file. The existing `tests/unit/test_steering.py` tests `push_steering_message` and `pop_all_steering_messages` which are called by the new script but not modified.

## Rabbit Holes

- **MCP server approach**: Building an MCP tool server for this is overengineered. PM session has bash access; a script is simpler, cheaper in context, and equally capable
- **Automatic fan-out**: Broadcasting steering to all children sounds useful but violates the explicit-action principle. Defer to a separate issue if ever needed
- **Bidirectional communication**: Dev session steering back to PM session is a different pattern (result reporting). Do not conflate
- **target_agent field enforcement**: The `target_agent` field in steering payloads is stored but not filtered. Enforcing it is a separate concern (#329 addressed fidelity modes)

## Risks

### Risk 1: Parent session ID availability in PM session context
**Impact:** If the PM session doesn't know its own session ID, it cannot pass `--parent-id` for validation
**Mitigation:** PM session's session ID is available via `CLAUDE_SESSION_ID` env var (set by `sdk_client.py`). Verify this is set for PM sessions, not just Dev sessions. If not, add it.

### Risk 2: Child session completes between steering push and consumption
**Impact:** Steering message sits in Redis queue unconsumed
**Mitigation:** This is already handled by existing infrastructure -- `clear_steering_queue` is called on session completion. The message is cleaned up. No orphaned keys.

## Race Conditions

### Race 1: Steering pushed after child session ends but before queue cleanup
**Location:** `agent/steering.py` push vs. job completion cleanup in `agent/job_queue.py`
**Trigger:** PM session pushes steering message at the exact moment a Dev session completes
**Data prerequisite:** Dev session must be in `status="running"` for steering to be meaningful
**State prerequisite:** The steering queue must be checked before it is cleared on completion
**Mitigation:** The script validates `status="running"` before pushing. If the session ends between validation and push, the message sits in the queue until `clear_steering_queue` runs on completion. No data loss, no corruption -- the message is simply never consumed (which is correct behavior since the session ended).

## No-Gos (Out of Scope)

- Automatic steering fan-out to all children (always explicit, per-child)
- MCP server registration for this tool (bash script is sufficient)
- Bidirectional Dev session-to-PM session communication
- Enforcement of `target_agent` field filtering in the watchdog
- UI/Telegram integration for parent-child steering (this is PM session-internal)

## Update System

No update system changes required -- this feature adds a new script (`scripts/steer_child.py`) that is automatically available after `git pull`. No new dependencies, config files, or migration steps.

## Agent Integration

No MCP server integration required. The steering tool is a bash-callable script that PM session invokes directly via its existing bash tool access. The bridge does not need changes -- it already has its own steering path for Telegram reply messages. This feature is purely a new caller (PM session) using existing infrastructure (`push_steering_message`).

## Documentation

- [ ] Update `docs/features/steering-queue.md` to document parent-child steering as a new steering path alongside bridge steering
- [ ] Update `docs/features/pm-dev-session-architecture.md` to document the steering capability in the PM/Dev session relationship section
- [ ] Add entry to `docs/features/README.md` index table if not already covered

## Success Criteria

- [ ] PM session can push a steering message to a running child Dev session via `python scripts/steer_child.py`
- [ ] The steering message appears in the child's context at the next tool call (existing watchdog behavior)
- [ ] Invalid targets (non-existent sessions, non-children) are rejected with clear error and non-zero exit code
- [ ] Abort steering is supported via `--abort` flag
- [ ] `--list` flag shows active child Dev sessions
- [ ] No automatic fan-out -- steering is always an explicit PM session action
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (steering-script)**
  - Name: steering-builder
  - Role: Implement `scripts/steer_child.py` and unit tests
  - Agent Type: builder
  - Resume: true

- **Validator (steering-validation)**
  - Name: steering-validator
  - Role: Verify script works end-to-end with validation logic
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Using core tier only: builder, validator, documentarian.

## Step by Step Tasks

### 1. Build steering script and tests
- **Task ID**: build-steering-script
- **Depends On**: none
- **Validates**: tests/unit/test_steer_child.py (create)
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/steer_child.py` with argparse CLI: `--session-id`, `--message`, `--parent-id`, `--abort`, `--list`
- Implement parent-child validation using `AgentSession.query.get()` and `parent_chat_session_id` check
- Implement `--list` to show active children via `AgentSession.get_dev_sessions()` filtered to `status="running"`
- Call `push_steering_message()` on validation success
- Create `tests/unit/test_steer_child.py` testing: valid steering, invalid child, non-child rejection, abort flag, empty message rejection, list output

### 2. Verify CLAUDE_SESSION_ID availability
- **Task ID**: verify-env-var
- **Depends On**: none
- **Assigned To**: steering-builder
- **Agent Type**: builder
- **Parallel**: true
- Check that `sdk_client.py` sets `CLAUDE_SESSION_ID` for PM session processes (not just Dev sessions)
- If missing, add it to the environment setup in `_build_env()`

### 3. Validate implementation
- **Task ID**: validate-steering
- **Depends On**: build-steering-script, verify-env-var
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify script exits 0 on valid steering, non-zero on all error paths
- Verify `--list` output format is usable by PM session
- Run full test suite to confirm no regressions

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-steering
- **Assigned To**: steering-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/steering-queue.md` with parent-child steering section
- Update `docs/features/pm-dev-session-architecture.md` with steering capability
- Update `docs/features/README.md` index if needed

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: steering-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_steer_child.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check scripts/steer_child.py` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/steer_child.py` | exit code 0 |
| Script help works | `python scripts/steer_child.py --help` | exit code 0 |
| Script rejects empty | `python scripts/steer_child.py --session-id x --message "" --parent-id y 2>&1` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue is well-specified with clear acceptance criteria, the infrastructure is mature, and the solution approach is straightforward. Ready for implementation.
