---
status: Planning
type: chore
appetite: Medium
owner: valorengels
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/750
last_comment_id:
---

# Bridge/Worker Separation

## Problem

`bridge/telegram_bridge.py` acts as both a Telegram I/O layer and a session execution engine. Despite a prior extraction effort (PR #737 / issue #731), the bridge still calls worker lifecycle functions directly at startup and runtime.

**Current behavior:**

At bridge startup (`telegram_bridge.py` lines 1885–2004):
- Imports and calls `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `_agent_session_health_loop` from `agent/agent_session_queue.py`
- Calls `AgentSession.rebuild_indexes()` directly
- Falls back to spawning `_agent_session_health_loop` task if `ReflectionScheduler` fails

At runtime (lines 2069–2102), a heartbeat loop polls for orphaned pending sessions and calls `_ensure_worker(cid)` — duplicating work the standalone worker already does. It also calls `_cleanup_orphaned_claude_processes()` every 5 minutes.

Because both bridge and worker call `_recover_interrupted_agent_sessions_startup()` and `_ensure_worker()` at startup with no coordination, either can win the race, or both can run simultaneously — causing duplicate session recovery and double-spawned worker loops.

**Desired outcome:**

- Bridge does exactly two things: receive Telegram messages and deliver responses. It enqueues `AgentSession` records to Redis and registers output callbacks. No execution function imports.
- `worker/__main__.py` is the single entry point for index rebuild, startup recovery, orphaned process cleanup, per-chat worker spawning, and health loop.
- One canonical startup recovery path — no duplication, no races.
- CLI tools support listing by priority/FIFO position, bumping priority, and canceling sessions.
- `docs/features/bridge-worker-architecture.md` documents the final separation.

## Prior Art

- **PR #737** (merged 2026-04-06): "Extract standalone worker service from bridge monolith" — Created `worker/__main__.py` but did not strip the bridge of its execution function calls. Left bridge coupling intact.
- **Issue #741**: "Persistent event loop and graceful shutdown for the worker service" — Improved worker robustness but did not remove bridge coupling.

Both prior efforts moved forward without enforcing the import boundary, so the bridge retained its execution responsibilities.

## Spike Results

No spikes required — the code is fully readable and the scope is well-defined by the issue recon.

## Data Flow

**After separation**, the only paths across the bridge/worker boundary are:

1. **Entry point**: Telegram message arrives at bridge
2. **Bridge**: Calls `enqueue_agent_session(...)` — writes `AgentSession` record to Redis with `status=pending`
3. **Redis**: Acts as the single communication contract between processes
4. **Worker**: Polls Redis for pending sessions; calls `_ensure_worker(chat_id)` internally; executes sessions via Claude Agent SDK
5. **Output**: `FileOutputHandler` writes session output; bridge reads via registered callbacks to deliver Telegram replies

**Bridge-only path**: Bridge registers output callbacks via `register_callbacks(project_key, handler=...)` — this is a legitimate bridge responsibility (it owns the delivery channel).

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #737 | Created `worker/__main__.py`, moved session execution there | Did not remove execution imports from bridge; bridge still calls `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `rebuild_indexes` at startup |
| Issue #741 | Added graceful shutdown and persistent event loop to worker | Addressed worker robustness only; bridge coupling was out of scope |

**Root cause pattern:** Each effort treated the worker as additive — creating worker capability without stripping bridge capability. The bridge/worker boundary was never enforced at the import level.

## Architectural Impact

- **New dependencies**: None — this is a removal and consolidation effort
- **Interface changes**: Bridge will only import `enqueue_agent_session`, `register_callbacks`, and `AgentSession` (for status reads). All other `agent_session_queue` imports are removed from the bridge.
- **Coupling**: Significantly decreases coupling. Bridge becomes a pure I/O adapter.
- **Data ownership**: Worker exclusively owns session lifecycle. Bridge owns Telegram I/O.
- **Reversibility**: Moderate difficulty — reverting would require re-adding execution calls to bridge. But forward direction is clearly correct.

The `agent/agent_session_queue.py` module (~3168 lines) contains both data model and execution engine. This plan does **not** split that file — execution functions remain there but are only called from `worker/`. A future refactor could extract worker execution functions to `worker/execution.py`, but that is out of scope.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1-2 (scope alignment on CLI extensions)
- Review rounds: 1 (code review + import boundary verification)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "import redis; redis.Redis().ping()"` | Session queue storage |
| Worker launchd service exists | `launchctl list | grep valor.worker` | Confirms worker runs as separate process |

Run all checks: `python scripts/check_prerequisites.py docs/plans/bridge-worker-separation.md`

## Solution

### Key Elements

- **Bridge strip**: Remove all execution function imports from `telegram_bridge.py` — `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `AgentSession.rebuild_indexes()`, `_agent_session_health_loop`, `_cleanup_orphaned_claude_processes`, and the orphaned-session heartbeat loop
- **Worker consolidation**: `worker/__main__.py` becomes the single entry point for all lifecycle responsibilities — index rebuild, startup recovery, worker spawning, health loop, orphaned process cleanup
- **CLI extension**: Extend `tools/valor_session.py` or `tools/agent_session_scheduler.py` with `list --by-priority`, `list --fifo`, `bump-priority`, and `cancel` subcommands
- **Architecture doc**: Create `docs/features/bridge-worker-architecture.md` documenting the final separation, Redis communication contract, and operator CLI usage

### Flow

**Startup (bridge process):**
Bridge connects to Telegram → registers output callbacks → begins receiving messages → enqueues AgentSessions to Redis → done.

**Startup (worker process):**
Worker starts → `AgentSession.rebuild_indexes()` → `_recover_interrupted_agent_sessions_startup()` → `_cleanup_orphaned_claude_processes()` → `_ensure_worker(chat_id)` for pending sessions → `_agent_session_health_loop()` as background task → processes sessions.

**Runtime message flow:**
Telegram message → bridge enqueues `AgentSession` (status=pending) → Redis → worker health loop or event wakes worker → `_ensure_worker(chat_id)` → session executes → output routed via registered callbacks → bridge delivers reply.

### Technical Approach

1. **Import audit**: grep all execution function references in `telegram_bridge.py` and remove each one
2. **Heartbeat cleanup**: Remove the orphaned-session heartbeat block (lines ~2069–2102) from the bridge's main loop; ensure worker health loop covers this
3. **Worker startup order**: In `worker/__main__.py`, establish a deterministic startup sequence: (1) index rebuild, (2) cleanup corrupted sessions, (3) startup recovery, (4) orphaned process cleanup, (5) start per-chat workers for pending sessions, (6) start health loop
4. **CLI extension**: Add `--sort priority` and `--position` flags to `agent_session_scheduler.py list`; add `bump` and `cancel` subcommands
5. **Test coverage**: New unit tests for worker startup sequence in `tests/unit/test_worker_entry.py`
6. **Docs/comment purge**: Search all `.md`, `.py`, and docstring content for references to bridge-embedded-worker pattern and update

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The removed bridge code has `except Exception` blocks around `_ensure_worker` calls — after removal, verify the worker's health loop has equivalent exception handling so no orphaned session silently goes unrecovered
- [ ] Worker startup sequence: each step (index rebuild, recovery, cleanup) must catch and log exceptions without aborting the entire startup

### Empty/Invalid Input Handling
- [ ] `_ensure_worker(chat_id)` with empty/None chat_id — verify worker handles gracefully (already tested in integration suite)
- [ ] Worker started with no pending sessions — verify health loop starts without error

### Error State Rendering
- [ ] If worker is not running when bridge starts, sessions queue in Redis but no error is surfaced — this is acceptable behavior; document it in the architecture doc

## Test Impact

- [ ] `tests/unit/test_worker_entry.py::test_no_module_level_bridge_imports` — UPDATE: extend to also assert bridge does not import `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `_agent_session_health_loop`, `_cleanup_orphaned_claude_processes`
- [ ] `tests/unit/test_worker_entry.py` — ADD: new tests for worker-only startup sequence (index rebuild → recovery → cleanup → worker spawn → health loop)
- [ ] `tests/integration/test_worker_drain.py` — REVIEW: ensure drain tests still pass after bridge no longer calls `_ensure_worker` directly
- [ ] `tests/unit/test_bridge_logic.py` — UPDATE: if any tests mock execution function calls from bridge startup, update to reflect removal

## Rabbit Holes

- **Splitting `agent/agent_session_queue.py`**: This 3168-line file mixes data model and execution engine. Splitting it into `agent/session_model.py` + `worker/execution.py` is tempting but a separate large refactor. Out of scope here.
- **ReflectionScheduler ownership**: The `ReflectionScheduler` currently starts in the bridge. Moving it to the worker is related but separate — it may have bridge-specific dependencies. Out of scope.
- **`monitoring/session_watchdog.py`**: External watchdog loop also has some overlap with the worker health loop. Consolidating these is a separate concern.
- **Bridge health/resilience modules**: `bridge/health.py` and `bridge/resilience.py` are correctly bridge-owned (they monitor bridge's external dependencies: Telegram, Redis, Anthropic). Do not move these.

## Risks

### Risk 1: Worker not running when bridge starts
**Impact:** Sessions queue in Redis but are never processed until worker starts. No error surfacing to operators.
**Mitigation:** Document in `docs/features/bridge-worker-architecture.md` that worker must always run alongside bridge. The existing launchd watchdog handles auto-restart of worker.

### Risk 2: Startup race between bridge and worker processes
**Impact:** Both try to read/write session states simultaneously on startup.
**Mitigation:** After removal, only worker calls `_recover_interrupted_agent_sessions_startup()`. Bridge never calls it. Redis operations in Popoto are atomic at the key level. Index rebuild is idempotent (SCAN-based). No coordination mechanism needed beyond the removal itself.

### Risk 3: Heartbeat removal leaves orphaned sessions unrescued
**Impact:** Sessions stuck in `pending` state with no worker to pick them up.
**Mitigation:** Worker's `_agent_session_health_loop` already handles orphaned session detection. Verify health loop interval is short enough (currently ~60s). Test explicitly.

## Race Conditions

### Race 1: Simultaneous startup recovery
**Location:** `bridge/telegram_bridge.py` lines 1905, `worker/__main__.py` line 152
**Trigger:** Bridge and worker both start within a few seconds of each other; both call `_recover_interrupted_agent_sessions_startup()` before the other completes
**Data prerequisite:** Running sessions in Redis from previous process
**State prerequisite:** Both processes have Redis connections
**Mitigation:** After this change, only `worker/__main__.py` calls this function. Bridge never calls it. Race eliminated by removal.

### Race 2: Worker spawn duplication
**Location:** `telegram_bridge.py` heartbeat + `worker/__main__.py` startup
**Trigger:** Both bridge heartbeat and worker startup call `_ensure_worker(chat_id)` for the same chat
**Data prerequisite:** Pending session in Redis for the chat
**State prerequisite:** `_active_workers` dict is process-local (each process has its own)
**Mitigation:** Since bridge and worker are separate OS processes, `_active_workers` is separate per-process. After removal, bridge never calls `_ensure_worker`. Race eliminated.

## No-Gos (Out of Scope)

- Merging bridge and worker into a single process
- Splitting `agent/agent_session_queue.py` into data model + execution submodules
- Moving `ReflectionScheduler` from bridge to worker
- Consolidating `monitoring/session_watchdog.py` with worker health loop
- Adding new session execution features to the worker beyond what already exists
- Changing the Redis data model or `AgentSession` schema

## Update System

The bridge and worker are separate launchd services (`scripts/valor-service.sh`). After this change:
- The worker **must** be running for any sessions to execute. The update script should verify both services are running after deployment.
- No new dependencies or config files are introduced.
- No migration steps needed — sessions already in Redis will be picked up by worker's startup recovery.
- Update `scripts/remote-update.sh` to verify worker service is running post-deploy alongside bridge.

## Agent Integration

No agent integration required — this is a bridge/worker internal refactor. The Redis enqueue interface (`enqueue_agent_session`) and callback registration (`register_callbacks`) remain unchanged. MCP servers and `.mcp.json` are unaffected.

## Documentation

- [ ] Create `docs/features/bridge-worker-architecture.md` describing the final separation, Redis communication contract, startup sequence, and operator CLI usage
- [ ] Update `docs/features/bridge-module-architecture.md` to reflect bridge's reduced responsibilities
- [ ] Update `docs/features/agent-session-queue.md` to reflect that execution functions are worker-only
- [ ] Add entry to `docs/features/README.md` index table for `bridge-worker-architecture.md`
- [ ] Purge all doc/comment references to bridge-embedded-worker pattern (bridge calling `_ensure_worker`, bridge-owned health loop)

## Success Criteria

- [ ] `bridge/telegram_bridge.py` imports zero execution functions: no `_ensure_worker`, no `_recover_interrupted_agent_sessions_startup`, no `_agent_session_health_loop`, no `_cleanup_orphaned_claude_processes`
- [ ] `worker/__main__.py` is the single entry point for index rebuild, startup recovery, orphaned process cleanup, worker spawning, and health loop
- [ ] No startup race condition: only worker calls recovery/spawn functions
- [ ] `tools/valor_session.py` or `tools/agent_session_scheduler.py` supports listing by priority/FIFO position, bumping priority, and canceling
- [ ] `docs/features/bridge-worker-architecture.md` created with Redis contract and operator CLI docs
- [ ] No existing doc, comment, or docstring references old embedded-worker pattern
- [ ] All existing tests pass (`pytest tests/ -x -q`)
- [ ] New unit tests cover worker-only startup sequence

## Team Orchestration

### Team Members

- **Builder (bridge-strip)**
  - Name: bridge-stripper
  - Role: Remove all execution function imports and calls from `telegram_bridge.py`; remove orphaned-session heartbeat block
  - Agent Type: builder
  - Resume: true

- **Builder (worker-consolidation)**
  - Name: worker-consolidator
  - Role: Consolidate startup sequence in `worker/__main__.py`; add `AgentSession.rebuild_indexes()` and `_cleanup_orphaned_claude_processes()` calls; verify health loop coverage
  - Agent Type: builder
  - Resume: true

- **Builder (cli-extension)**
  - Name: cli-extender
  - Role: Extend `tools/agent_session_scheduler.py` with list-by-priority, FIFO position, bump-priority, and cancel subcommands
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-writer
  - Role: Update `test_worker_entry.py` import boundary test; write new unit tests for worker startup sequence
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Create `docs/features/bridge-worker-architecture.md`; update related docs; purge stale references
  - Agent Type: documentarian
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Verify import boundary, run full test suite, confirm docs created
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Strip bridge of execution functions
- **Task ID**: build-bridge-strip
- **Depends On**: none
- **Validates**: `grep -n "_ensure_worker\|_recover_interrupted\|_agent_session_health_loop\|_cleanup_orphaned" bridge/telegram_bridge.py | wc -l` → 0
- **Assigned To**: bridge-stripper
- **Agent Type**: builder
- **Parallel**: true
- Remove imports of `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `_agent_session_health_loop`, `_cleanup_orphaned_claude_processes` from `telegram_bridge.py`
- Remove `AgentSession.rebuild_indexes()` call from bridge startup
- Remove the orphaned-session heartbeat block (lines ~2069–2102) that calls `_ensure_worker(cid)`
- Remove zombie cleanup call from bridge heartbeat
- Remove fallback `_agent_session_health_loop` task creation (lines ~1997–2004)
- Keep all bridge-appropriate code: `enqueue_agent_session`, `register_callbacks`, `AgentSession` status reads

### 2. Consolidate worker startup sequence
- **Task ID**: build-worker-consolidation
- **Depends On**: none
- **Validates**: `worker/__main__.py` startup sequence: rebuild_indexes → cleanup_corrupted → recover_interrupted → cleanup_orphaned_processes → ensure_workers → health_loop
- **Assigned To**: worker-consolidator
- **Agent Type**: builder
- **Parallel**: true
- Add `AgentSession.rebuild_indexes()` call to `worker/__main__.py` startup (currently only in bridge)
- Add `_cleanup_orphaned_claude_processes()` call to `worker/__main__.py` startup (currently only in bridge)
- Verify `_recover_interrupted_agent_sessions_startup()` is already called (it is)
- Verify `_agent_session_health_loop()` is already started as background task (it is)
- Ensure startup sequence is deterministic and logged at each step
- Move `_cleanup_orphaned_claude_processes` function definition from `telegram_bridge.py` to `agent/agent_session_queue.py` or `worker/__main__.py` so worker can call it without importing from bridge

### 3. Extend CLI tools
- **Task ID**: build-cli-extension
- **Depends On**: none
- **Validates**: `python -m tools.agent_session_scheduler list --help` shows priority/position flags; `bump` and `cancel` subcommands work
- **Assigned To**: cli-extender
- **Agent Type**: builder
- **Parallel**: true
- Add `--sort {priority,fifo,status}` flag to `list` subcommand in `tools/agent_session_scheduler.py`
- Add `--position` output column showing FIFO rank within priority band
- Add `bump` subcommand: `python -m tools.agent_session_scheduler bump --id <ID> --priority <N>`
- Add `cancel` subcommand: `python -m tools.agent_session_scheduler cancel --id <ID>`
- Ensure `tools/valor_session.py` `list` command shows priority field

### 4. Write and update tests
- **Task ID**: build-tests
- **Depends On**: build-bridge-strip, build-worker-consolidation
- **Validates**: `pytest tests/unit/test_worker_entry.py -v` passes; new startup sequence tests pass
- **Assigned To**: test-writer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update `test_no_module_level_bridge_imports` in `test_worker_entry.py` to also assert bridge does not import `_ensure_worker`, `_recover_interrupted_agent_sessions_startup`, `_agent_session_health_loop`, `_cleanup_orphaned_claude_processes`
- Add new test class `TestWorkerStartupSequence` with tests for: index rebuild called, corrupted session cleanup called, startup recovery called, orphaned process cleanup called, workers spawned for pending sessions, health loop started
- Verify `tests/integration/test_worker_drain.py` still passes

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-bridge-strip, build-worker-consolidation, build-cli-extension
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/bridge-worker-architecture.md` with: architecture diagram, startup sequence for each process, Redis communication contract, CLI operator commands
- Update `docs/features/bridge-module-architecture.md` to remove references to bridge-embedded-worker
- Update `docs/features/agent-session-queue.md` to note execution functions are worker-only callers
- Add entry to `docs/features/README.md` index
- Search and purge stale doc/comment/docstring references to bridge-owned execution

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -n "_ensure_worker\|_recover_interrupted\|_agent_session_health_loop\|_cleanup_orphaned" bridge/telegram_bridge.py` — expect 0 results
- Run `pytest tests/ -x -q` — expect all pass
- Verify `docs/features/bridge-worker-architecture.md` exists and is non-empty
- Verify `docs/features/README.md` has entry for bridge-worker-architecture
- Confirm no stale references in docs: `grep -r "bridge.*_ensure_worker\|bridge.*embedded.worker" docs/`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Bridge has no execution imports | `grep -c "_ensure_worker\|_recover_interrupted\|_agent_session_health_loop" bridge/telegram_bridge.py` | output contains 0 |
| Worker startup sequence complete | `grep -c "rebuild_indexes\|_recover_interrupted\|_cleanup_orphaned\|_agent_session_health_loop" worker/__main__.py` | output > 3 |
| Architecture doc exists | `test -f docs/features/bridge-worker-architecture.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions — the scope is fully defined by the issue recon. All constraints (separate processes, Redis-only contract, extend not replace CLI tools) are explicit.
