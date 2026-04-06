---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-04-06
tracking: https://github.com/tomcounsell/ai/issues/731
last_comment_id:
---

# Extract Standalone Worker Service from Bridge Monolith

## Problem

The Telegram bridge (`bridge/telegram_bridge.py`, 2110 lines) is a monolith that owns three distinct responsibilities: platform I/O, session execution, and background services. The session execution engine (`agent/agent_session_queue.py`, 2853 lines) is structurally independent but can only run when the bridge starts it.

**Current behavior:**

- The worker loop (`_worker_loop`) is spawned by `_ensure_worker()`, which is only called from bridge code during startup (line ~1897) or when sessions are enqueued
- Output callbacks (`_send_callbacks`) are registered by the bridge at startup -- if missing, agent output is silently dropped (line 2005: `if not send_cb: return`)
- Sessions pushed to Redis via `python -m tools.agent_session_scheduler push` sit as `pending` records indefinitely with no worker to pick them up on non-bridge machines
- `agent/agent_session_queue.py` has 2 module-level imports from `bridge/` (`bridge.response` for reaction constants, `bridge.session_logs` for snapshots) and 6 lazy imports scattered through the execution path

**Desired outcome:**

A standalone `python -m worker` entry point that processes AgentSession records from Redis without requiring Telegram. Developer workstations run just the worker. Bridge machines run bridge + embedded worker (backward compatible). New platform bridges (email, Slack) become thin I/O adapters that enqueue work to the same shared worker.

## Prior Art

No prior issues found related to extracting the worker as a standalone service. Related work:
- **#495**: Bridge resilience -- established graceful degradation patterns for dependency outages
- **#609**: AgentSession field cleanup -- tracks renaming `initial_telegram_message` (excluded from this scope)
- **#727**: Startup recovery -- fixed orphan SDK subprocess issue during `_recover_interrupted_agent_sessions_startup()`
- **#730**: Session re-enqueue loop -- fixed terminal-status guard in the intake path

## Data Flow

Current session lifecycle, traced end-to-end:

1. **Entry point**: Telegram message arrives via Telethon `events.NewMessage` handler in `bridge/telegram_bridge.py`
2. **Bridge routing**: `bridge/routing.py` maps chat group to project, `bridge/session_router.py` determines session type (PM/Dev/Teammate)
3. **Enqueue**: Bridge calls `agent.agent_session_queue.enqueue_agent_session()` -- creates `AgentSession` record in Redis, calls `_ensure_worker(chat_id)`
4. **Worker loop**: `_worker_loop(chat_id)` pops sessions via `_pop_agent_session()`, calls `_execute_agent_session()`
5. **Enrichment**: Worker calls `bridge.enrichment.enrich_message()` using Telegram client (lazy import, gracefully skipped if unavailable)
6. **Execution**: Worker calls `agent.sdk_client.get_agent_response_sdk()` which spawns Claude Code subprocess
7. **Output routing**: `send_to_chat()` closure calls `_send_callbacks[project_key]()` -- the callback was registered by the bridge and sends via Telegram
8. **Reactions**: `_reaction_callbacks[project_key]()` sets emoji reactions on the original Telegram message
9. **Lifecycle**: `bridge.session_logs.save_session_snapshot()` records state at each transition

**After this change**: Steps 1-3 remain bridge-only. Steps 4-9 run in the worker process. The bridge registers its Telegram-specific callbacks (step 7-8) with the worker at startup. Headless workers use a file-logging fallback for output.

## Architectural Impact

- **New dependencies**: None. Redis (Popoto) remains the only coordination layer.
- **Interface changes**: New `OutputHandler` protocol formalizes the callback shape. `register_callbacks()` already accepts this shape -- the protocol makes it explicit.
- **Coupling**: Significantly decreases coupling. `agent/agent_session_queue.py` loses all module-level `bridge/` imports. Bridge becomes a thin I/O adapter.
- **Data ownership**: No change. Redis remains the single source of truth for session state.
- **Reversibility**: High. The worker entry point is additive. The bridge can continue running its embedded worker indefinitely.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (scope alignment on callback protocol, service boundaries)
- Review rounds: 2+ (architecture review, backward compatibility verification)

## Prerequisites

No prerequisites -- this work has no external dependencies beyond what already exists.

## Solution

### Key Elements

- **OutputHandler protocol**: Formal typed protocol replacing the ad-hoc callback registration, so new bridges can implement output routing without reading Telegram code
- **Worker entry point** (`worker/__main__.py`): Standalone process that loads config, starts worker loops, and runs the health monitor -- no Telegram dependency
- **Bridge import decoupling**: Move reaction constants and session snapshot logic out of `bridge/` into `agent/` or shared locations so the worker has zero `bridge/` module-level imports
- **Fallback output handler**: File-based logger for headless worker operation when no platform bridge has registered callbacks
- **Launchd service**: `com.valor.worker` plist following the existing bridge pattern

### Flow

**Standalone worker path:**
`python -m worker` → load `projects.json` → recover interrupted sessions → start `_worker_loop` per project → register file-logging fallback handler → process pending sessions → write output to `logs/worker/`

**Bridge + embedded worker path (backward compatible):**
`python bridge/telegram_bridge.py` → create Telegram client → register Telegram callbacks via `register_callbacks()` → recover interrupted sessions → start workers → process sessions with Telegram output

**New platform bridge path (future):**
`python -m email_bridge` → register email callbacks via `register_callbacks()` → enqueue sessions → worker routes output through email handler

### Technical Approach

- **Phase 1: Decouple imports** -- Move `REACTION_COMPLETE/ERROR/SUCCESS` constants to `agent/constants.py`. Move `save_session_snapshot` to `agent/session_logs.py` (re-export from `bridge/session_logs.py` for backward compat). Guard remaining 6 lazy `bridge/` imports with `ImportError` handling alongside existing `try/except`.

- **Phase 2: Formalize OutputHandler** -- Create `agent/output_handler.py` with a `Protocol` class and a `FileOutputHandler` default implementation. Refactor `register_callbacks()` to accept an `OutputHandler` instance as an alternative to raw callables (backward compatible).

- **Phase 3: Worker entry point** -- Create `worker/__main__.py` that imports from `agent/agent_session_queue.py`, loads project config from `projects.json`, registers `FileOutputHandler` as default, and starts workers. Include SIGTERM handling for graceful shutdown.

- **Phase 4: Service infrastructure** -- Create `scripts/install_worker.sh` and `com.valor.worker.plist` following the bridge pattern. Add `worker` commands to `valor-service.sh`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `send_to_chat` when `send_cb is None` currently returns silently -- add test asserting file-logging fallback is invoked
- [ ] `_execute_agent_session` enrichment failure path already has `try/except` with warning log -- add test for headless (no Telegram client) path
- [ ] Worker startup with missing `projects.json` -- assert graceful error with clear message

### Empty/Invalid Input Handling
- [ ] Worker started with zero projects in config -- assert clean exit with log message
- [ ] `FileOutputHandler.send()` receives empty string -- assert no crash, no empty file write
- [ ] `register_callbacks()` called with None handler -- assert defensive rejection

### Error State Rendering
- [ ] Worker log output for failed sessions includes session ID and error details
- [ ] Dashboard correctly shows sessions processed by headless worker (no Telegram metadata)

## Test Impact

- [ ] `tests/unit/test_nudge_loop.py` -- UPDATE: imports of `REACTION_*` constants move from `bridge.response` to `agent.constants`
- [ ] `tests/unit/test_agent_session_queue_async.py` -- UPDATE: mock paths change for moved imports
- [ ] `tests/integration/test_worker_drain.py` -- UPDATE: may need adjusted imports for moved constants
- [ ] `tests/unit/test_duplicate_delivery.py` -- UPDATE: `REACTION_*` import paths change
- [ ] `tests/unit/test_session_completion_zombie.py` -- UPDATE: `REACTION_*` import paths change
- [ ] `tests/unit/test_recovery_respawn_safety.py` -- UPDATE: `register_callbacks` mock path may change
- [ ] `tests/integration/test_agent_session_health_monitor.py` -- UPDATE: imports of moved functions
- [ ] `tests/integration/test_silent_failures.py` -- UPDATE: `register_callbacks` and callback mock paths

## Rabbit Holes

- **Full bridge refactor into microservices** -- This plan extracts the worker only. The bridge remains a monolith for platform I/O. Breaking the bridge further (routing, catchup, reconciler as separate services) is a separate project.
- **Multi-machine Redis coordination** -- The worker assumes single-machine operation. Distributed locking across machines is out of scope. Workers on different machines can safely process different `chat_id` partitions but should not share the same partition.
- **AgentSession field renames** -- Already tracked in #609. Do not rename `telegram_message_id` or `initial_telegram_message` in this issue.
- **Enrichment refactor** -- Enrichment stays as a pluggable lazy import. Do not attempt to make it a formal plugin system.

## Risks

### Risk 1: Silent output loss during transition
**Impact:** Sessions processed by headless worker produce output that goes nowhere if the fallback handler has a bug.
**Mitigation:** `FileOutputHandler` logs to `logs/worker/{session_id}.log` and is tested explicitly. The existing `if not send_cb: return` path already silently drops output -- the fallback handler is strictly better.

### Risk 2: Bridge backward compatibility regression
**Impact:** Existing bridge deployments stop processing sessions if the import restructuring breaks something.
**Mitigation:** Re-exports from old import paths (e.g., `bridge.response.REACTION_COMPLETE` re-exports from `agent.constants`). Integration tests verify the bridge path end-to-end.

### Risk 3: Health monitor not running in standalone worker
**Impact:** Sessions get stuck with no health check to recover them.
**Mitigation:** Worker entry point starts `_agent_session_health_loop()` just like the bridge does. Test verifies the health loop starts.

## Race Conditions

### Race 1: Two workers (bridge + standalone) for the same chat_id
**Location:** `agent/agent_session_queue.py` lines 1446-1462 (`_ensure_worker`)
**Trigger:** Bridge starts its embedded worker AND a standalone worker runs on the same machine, both calling `_ensure_worker` for the same chat.
**Data prerequisite:** `_active_workers` dict is process-local, so two processes have independent worker sets.
**State prerequisite:** Only one process should own a given chat_id's worker loop.
**Mitigation:** Document that standalone worker and bridge should not run simultaneously for the same project on the same machine. Redis-level locking is out of scope (rabbit hole). In practice, dev workstations run only worker, bridge machines run bridge+embedded worker.

### Race 2: Callback registration timing during worker startup
**Location:** `agent/agent_session_queue.py` line 2005 (`if not send_cb: return`)
**Trigger:** Worker pops and executes a session before the bridge has registered callbacks.
**Data prerequisite:** Callbacks must be registered before sessions produce output.
**State prerequisite:** Bridge must register callbacks before recovery populates the queue.
**Mitigation:** No change needed -- the bridge already registers callbacks before calling `_recover_interrupted_agent_sessions_startup()`. Standalone worker registers `FileOutputHandler` before starting workers. Existing behavior (silent drop) is replaced by file logging.

## No-Gos (Out of Scope)

- AgentSession field renames (`initial_telegram_message` -> generic) -- tracked in #609
- Full `bridge/routing.py` refactor -- config loading works fine with shared import
- Multi-machine distributed locking -- out of scope, document the constraint
- Enrichment plugin system -- lazy import with graceful degradation is sufficient
- New bridge implementations (email, Slack) -- this plan enables them, does not build them
- Dashboard changes -- dashboard already reads from Redis AgentSession records, which are platform-agnostic

## Update System

The update script (`scripts/remote-update.sh`) and update skill need changes:
- Add worker service management: `launchctl stop com.valor.worker` before update, restart after
- The `valor-service.sh` script needs `worker start/stop/restart/status` commands alongside the existing bridge commands
- New `scripts/install_worker.sh` must be run during first deployment setup on dev workstations
- Bridge machines continue using existing update flow; worker restart is additive

## Agent Integration

No agent integration required -- this is an infrastructure change to the session execution layer. The agent (Claude Code subprocess) is unaware of whether it was spawned by a bridge-embedded worker or a standalone worker. No MCP server changes, no `.mcp.json` changes, no new tools.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/worker-service.md` describing the standalone worker architecture, entry points, and deployment topology
- [ ] Update `docs/features/README.md` index table with worker service entry
- [ ] Update `docs/deployment.md` with new service topology (worker + bridge + web + reflections)

### Inline Documentation
- [ ] Docstrings on `OutputHandler` protocol methods
- [ ] Updated module docstring for `agent/agent_session_queue.py` reflecting decoupled architecture
- [ ] `worker/__main__.py` module docstring with usage examples

## Success Criteria

- [ ] `python -m worker` starts and processes pending AgentSession records without Telegram
- [ ] `python -m tools.agent_session_scheduler push --message "test"` enqueued session is picked up by standalone worker
- [ ] Worker writes agent output to `logs/worker/` when no bridge callbacks registered
- [ ] `agent/agent_session_queue.py` has zero module-level imports from `bridge/`
- [ ] Telegram bridge continues working identically with embedded worker
- [ ] `OutputHandler` protocol is defined in `agent/output_handler.py`
- [ ] Worker runs as `com.valor.worker` launchd service
- [ ] Dashboard shows sessions from both sources identically
- [ ] All existing tests pass with updated import paths
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (import-decoupling)**
  - Name: decoupler
  - Role: Move constants and session_logs out of bridge/, update imports, add re-exports
  - Agent Type: builder
  - Resume: true

- **Builder (output-handler)**
  - Name: output-builder
  - Role: Create OutputHandler protocol, FileOutputHandler, refactor register_callbacks
  - Agent Type: builder
  - Resume: true

- **Builder (worker-entry)**
  - Name: worker-builder
  - Role: Create worker/__main__.py, service scripts, launchd plist
  - Agent Type: builder
  - Resume: true

- **Test Engineer (worker-tests)**
  - Name: test-engineer
  - Role: Write tests for standalone worker, output handler, import decoupling
  - Agent Type: test-engineer
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify bridge backward compat, dashboard rendering, end-to-end session flow
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Create worker-service.md, update deployment.md and README index
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Decouple bridge imports from agent_session_queue
- **Task ID**: build-decouple
- **Depends On**: none
- **Validates**: `tests/unit/test_nudge_loop.py`, `tests/unit/test_agent_session_queue_async.py`, `tests/unit/test_duplicate_delivery.py`
- **Assigned To**: decoupler
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/constants.py` with `REACTION_SUCCESS`, `REACTION_COMPLETE`, `REACTION_ERROR` constants
- Move `save_session_snapshot()` to `agent/session_logs.py` (or make it importable from agent/)
- Update `bridge/response.py` and `bridge/session_logs.py` to re-export from new locations
- Update `agent/agent_session_queue.py` module-level imports to use `agent.constants` and `agent.session_logs`
- Ensure all 6 lazy `bridge/` imports handle `ImportError` (add where missing)
- Update all test files that import `REACTION_*` from `bridge.response`

### 2. Create OutputHandler protocol and FileOutputHandler
- **Task ID**: build-output-handler
- **Depends On**: none
- **Validates**: `tests/unit/test_output_handler.py` (create)
- **Assigned To**: output-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/output_handler.py` with `OutputHandler` Protocol class (send, react methods)
- Implement `FileOutputHandler` that writes to `logs/worker/{session_id}.log`
- Implement `LoggingOutputHandler` as a simple stderr fallback
- Refactor `register_callbacks()` to accept `OutputHandler` or raw callables (backward compat)
- Update `send_to_chat` to use fallback handler when `send_cb is None` instead of silent return

### 3. Create worker entry point
- **Task ID**: build-worker
- **Depends On**: build-decouple, build-output-handler
- **Validates**: `tests/unit/test_worker_entry.py` (create), `tests/integration/test_standalone_worker.py` (create)
- **Assigned To**: worker-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `worker/__init__.py` and `worker/__main__.py`
- Load project config from `projects.json` via `bridge.routing.load_config()` (shared, not bridge-specific)
- Register `FileOutputHandler` as default for each project
- Call `_recover_interrupted_agent_sessions_startup()` and `_ensure_worker()` per project
- Start `_agent_session_health_loop()` as background task
- Handle SIGTERM for graceful shutdown (same pattern as bridge)
- Add `__main__` guard with `asyncio.run(main())`

### 4. Create service infrastructure
- **Task ID**: build-service
- **Depends On**: build-worker
- **Validates**: manual verification of launchd plist
- **Assigned To**: worker-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `scripts/install_worker.sh` following `scripts/install_reflections.sh` pattern
- Create `com.valor.worker.plist` template (KeepAlive, log rotation, StandardOutPath)
- Add `worker start|stop|restart|status` commands to `scripts/valor-service.sh`
- Update `scripts/remote-update.sh` to stop/restart worker during updates

### 5. Write tests for worker service
- **Task ID**: build-tests
- **Depends On**: build-worker
- **Validates**: `tests/unit/test_output_handler.py`, `tests/unit/test_worker_entry.py`, `tests/integration/test_standalone_worker.py`
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Test `FileOutputHandler` writes output to correct log path
- Test `OutputHandler` protocol compliance
- Test worker entry point starts with valid config and exits cleanly with empty config
- Test `register_callbacks` accepts both `OutputHandler` and raw callables
- Test `send_to_chat` uses fallback handler when no bridge callback registered
- Test startup recovery runs before worker loops start

### 6. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-tests
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `python -m worker --help` or `python -m worker` starts without Telegram
- Verify all existing tests pass with updated imports
- Verify `bridge/telegram_bridge.py` still works (import paths, callback registration)
- Verify `REACTION_*` re-exports work from both old and new paths
- Check dashboard renders sessions from both sources

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/worker-service.md`
- Update `docs/features/README.md` index
- Update `docs/deployment.md` with four-service topology
- Update `CLAUDE.md` Quick Commands table with worker commands

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met
- Verify zero module-level bridge imports in `agent/agent_session_queue.py`
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Worker starts | `timeout 5 python -m worker --dry-run 2>&1` | output contains "worker" |
| No bridge imports | `grep -c "^from bridge\." agent/agent_session_queue.py` | output contains "0" |
| Re-exports work | `python -c "from bridge.response import REACTION_COMPLETE; print(REACTION_COMPLETE)"` | exit code 0 |
| OutputHandler exists | `python -c "from agent.output_handler import OutputHandler"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. Should the standalone worker support a `--project` flag to limit which projects it processes, or should it always process all projects from `projects.json`? (Leaning toward `--project` flag for dev workstation use.)
2. Should `bridge.routing.load_config()` be moved to a shared location (e.g., `config/projects.py`) since both the worker and bridge need it, or is importing from `bridge.routing` acceptable for the worker? (Leaning toward keeping it in `bridge.routing` with a re-export from `config/` to avoid a large move.)
