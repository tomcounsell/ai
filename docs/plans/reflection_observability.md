---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-03-27
tracking: https://github.com/tomcounsell/ai/issues/569
last_comment_id:
---

# Reflection Observability: Resource Guards, Log Rotation, Crash Detection

## Problem

The bridge was killed (SIGKILL, exit code -9) during the `daily-maintenance` reflection. The `docs_auditor` step made ~6,265 unbounded Anthropic API calls, generating a 37MB `bridge.log`. The process was killed externally with no traceback, no crash tracker record, and no alert. The bridge stayed down until manually discovered.

**Current behavior:**
- No reflection step has memory monitoring, API call caps, or hard timeouts
- `bridge.log` uses `logging.FileHandler` (unbounded growth)
- SIGKILL kills are uncatchable; crash tracker requires explicit `log_crash()` calls
- The watchdog detects the bridge is down but doesn't record the crash event
- A single reflection step can silently consume unbounded API calls, disk, and memory

**Desired outcome:**
- Every reflection step has resource guardrails (memory delta, wall-clock timeout)
- `bridge.log` rotates automatically and can't grow unbounded
- SIGKILL and OOM kills are detected and recorded by the crash tracker
- `docs_auditor` has a per-run API call cap
- Nasty behavior is detected before it kills the bridge

## Prior Art

- **Issue #566 (open)**: Regroup Reflections: 19 steps into 14 units -- structural refactor of step pipeline, does not address observability. PR #572 merged for this.
- **Issue #495 (closed)**: Bridge resilience: graceful degradation for dependency outages -- added circuit breakers for external service failures (PR #502)
- **Issue #510 (closed)**: Bridge crash-loop escalates Telegram FloodWait -- fixed crash-loop behavior but not resource-based kills
- **Issue #538 (closed)**: Reflection scheduler broken: Popoto ListField validation fails on save -- scheduler fix, unrelated to resource guards
- **PR #389**: Reflections as first-class objects with unified scheduler -- current scheduler architecture

No prior attempts to add resource guards or log rotation exist in this repo.

## Data Flow

1. **Entry point**: Bridge startup (`bridge/telegram_bridge.py`) configures root logger with `FileHandler("bridge.log")` and starts `ReflectionScheduler`
2. **Scheduler tick** (`agent/reflection_scheduler.py`): Every 60s, checks each reflection entry against its interval. Due reflections are dispatched.
3. **Function-type execution** (`execute_function_reflection`): Resolves dotted callable path, calls it. No timeout, no memory guard. Runs as asyncio task.
4. **Agent-type execution** (`_enqueue_agent_reflection`): Runs shell command via subprocess with 1h timeout.
5. **`daily-maintenance`**: Calls `scripts.reflections.run_reflections_async` -- a 14-unit pipeline where each unit runs sequentially. One unit (`documentation_audit`) invokes `DocsAuditor` which makes 1-2 API calls per doc file with no cap.
6. **Logging**: All output goes to root logger -> `FileHandler` -> `bridge.log`. No size limit, no rotation. A single run can write tens of MB.
7. **On crash**: If SIGKILL terminates the bridge, the process dies instantly. `crash_tracker.log_crash()` is never called. The watchdog (`com.valor.bridge-watchdog`, external launchd) detects the bridge is gone via `pgrep` on its next 60s tick but doesn't call `log_crash()`.

## Architectural Impact

- **New dependencies**: None (`psutil` is already in `pyproject.toml`)
- **Interface changes**: `ReflectionEntry` dataclass gains optional `timeout` field; `config/reflections.yaml` schema gains `timeout` key
- **Coupling**: No new coupling -- changes are additive within existing modules
- **Data ownership**: No changes to data ownership
- **Reversibility**: All changes are additive and independently revertible

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on thresholds)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites -- this work uses only existing dependencies (`psutil`, `logging.handlers`, `crash_tracker`).

## Solution

### Key Elements

- **Memory instrumentation**: `psutil` memory snapshots before/after each reflection in the scheduler
- **Timeout enforcement**: Per-reflection `timeout` field in YAML, enforced via `asyncio.wait_for()` for function-type reflections
- **Log rotation**: Switch `FileHandler` to `RotatingFileHandler` in `bridge/telegram_bridge.py`
- **Crash detection bridge**: Watchdog calls `crash_tracker.log_crash("sigkill_detected")` when it finds the bridge dead
- **API call cap**: `docs_auditor` gets a `max_api_calls` parameter that stops processing when reached

### Flow

**Reflection scheduled** -> Memory snapshot (before) -> `asyncio.wait_for(callable, timeout)` -> Memory snapshot (after) -> Log delta, duration, warnings -> State update

**Bridge crashes** -> Watchdog tick (60s) -> `pgrep` fails -> `log_crash("sigkill_detected")` -> Recovery escalation (existing)

### Technical Approach

- Add `timeout` field to `ReflectionEntry` dataclass with a per-type default (30 min function, 60 min agent)
- Wrap `execute_function_reflection()` in `asyncio.wait_for()` using the entry's timeout
- Use `psutil.Process(os.getpid()).memory_info().rss` for memory snapshots (cheap, no overhead)
- Log memory delta as structured field; warn at >100MB delta
- Replace `logging.FileHandler` with `logging.handlers.RotatingFileHandler(maxBytes=10*1024*1024, backupCount=5)` in `bridge/telegram_bridge.py`
- In `bridge_watchdog.py::check_bridge_health()`, when `is_bridge_running()` returns False, call `log_crash("bridge_dead_on_watchdog_check")`
- Add `max_api_calls` parameter to `DocsAuditor.__init__()` (default 50); decrement counter per API call, raise when exhausted

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `execute_function_reflection`: already has try/except, test that `asyncio.TimeoutError` from `wait_for` is caught and logged as error status
- [ ] `DocsAuditor` API call cap: test that exceeding the cap raises a clear exception (not silent truncation)
- [ ] Memory snapshot failure (e.g., `psutil` import error): test that reflection still runs (memory monitoring is best-effort)

### Empty/Invalid Input Handling
- [ ] `timeout: 0` or negative in YAML: test that validator rejects it
- [ ] `max_api_calls: 0` in DocsAuditor: test that it processes zero files gracefully

### Error State Rendering
- [ ] Memory warning logs include reflection name, delta, and absolute RSS
- [ ] Timeout errors in scheduler include the reflection name and configured timeout

## Test Impact

- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryLoading` -- UPDATE: add test for `timeout` field parsing from YAML
- [ ] `tests/unit/test_reflection_scheduler.py::TestRegistryLoading::test_load_registry_from_project` -- UPDATE: assert `timeout` field is present on loaded entries
- [ ] `tests/unit/test_bridge_watchdog.py` -- UPDATE: add test that `check_bridge_health` calls `log_crash()` when bridge process is not running
- [ ] `tests/unit/test_docs_auditor.py` -- UPDATE: add test for API call cap enforcement

## Rabbit Holes

- **Per-reflection API call counting via proxy/wrapper**: Tempting to build a generic API call counter wrapping the Anthropic client. Too complex -- only `docs_auditor` needs this, and a simple counter parameter is sufficient.
- **Parallel reflection execution**: Out of scope (mentioned in #566). This issue is about observability and safety, not performance.
- **Log aggregation / external monitoring**: Shipping logs to an external service is a separate concern. Rotation is sufficient for now.
- **Memory-based circuit breaker that kills reflections at 800MB**: Attractive but risky -- killing an in-progress reflection mid-execution can leave corrupted state. Warning logs are the right first step.

## Risks

### Risk 1: `asyncio.wait_for()` cancellation leaves state inconsistent
**Impact:** A reflection that's cancelled mid-execution may leave Redis state as "running" forever
**Mitigation:** The existing stuck-reflection detection in `tick()` (lines 316-327) already handles this case by resetting after 2x interval. Additionally, the `run_reflection` wrapper already catches all exceptions and calls `mark_completed` with error.

### Risk 2: `psutil.Process().memory_info()` overhead in tight scheduler loop
**Impact:** Adds ~0.1ms per call, called 2x per reflection per tick
**Mitigation:** Negligible overhead. Only called for reflections that actually execute (not every tick for every entry).

### Risk 3: RotatingFileHandler loses log data during rotation
**Impact:** Brief window where log writes during rotation could be lost
**Mitigation:** `RotatingFileHandler` is thread-safe and handles this internally. Standard library behavior, well-tested.

## Race Conditions

### Race 1: Watchdog logs crash while bridge is starting up
**Location:** `monitoring/bridge_watchdog.py::check_bridge_health()`
**Trigger:** Bridge process exits, watchdog checks, bridge starts restarting but `pgrep` runs in the gap
**Data prerequisite:** Bridge PID must be registered before watchdog check
**State prerequisite:** Bridge must be fully started (process visible to `pgrep`)
**Mitigation:** This is benign -- a false "crash" event is logged, but the crash tracker's pattern detection requires 3+ crashes in 30 minutes to trigger action. A single false positive is harmless. The watchdog already has a recovery lock mechanism to prevent cascading actions.

No other race conditions identified -- all changes are additive and operate on independent state.

## No-Gos (Out of Scope)

- Parallel reflection execution (separate concern, #566 territory)
- External log aggregation or monitoring service integration
- Memory-based automatic kill of runaway reflections (warning only in v1)
- Refactoring the docs_auditor to batch API calls (separate optimization)
- Adding resource guards to individual steps within `scripts/reflections.py` (only the scheduler-level wrapping)
- The `log-health-check` meta-reflection from the issue's Layer 3 -- deferred to a follow-up issue to keep this Medium appetite

## Update System

No update system changes required -- all changes are to existing Python modules within the project. `psutil` is already a dependency. No new config files, services, or migration steps needed.

## Agent Integration

No agent integration required -- this is bridge-internal infrastructure work. No new MCP servers, no changes to `.mcp.json`, no new tools exposed to the agent. The bridge imports are modified but the bridge already imports these modules.

## Documentation

- [ ] Update `docs/features/reflections.md` to document: timeout field in YAML schema, memory instrumentation behavior, log rotation configuration
- [ ] Update `CLAUDE.md` quick reference to note log rotation (remove any mention of unbounded log growth)
- [ ] Add entry to `docs/features/README.md` if reflections observability warrants its own section

## Success Criteria

- [ ] `psutil` memory snapshots logged (before/after) for every reflection execution in `reflection_scheduler.py`
- [ ] Memory delta > 100MB triggers a WARNING log with reflection name and delta
- [ ] `config/reflections.yaml` supports optional `timeout` field per reflection entry
- [ ] Function-type reflections wrapped in `asyncio.wait_for()` with configured timeout (default: 30 min)
- [ ] `bridge.log` uses `RotatingFileHandler` with 10MB max size and 5 backup files
- [ ] Bridge watchdog calls `crash_tracker.log_crash()` when it detects bridge process is dead
- [ ] `docs_auditor` has configurable per-run API call cap (default: 50) that stops processing when reached
- [ ] `python scripts/reflections.py --dry-run` completes without errors after changes
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (scheduler-guards)**
  - Name: scheduler-builder
  - Role: Add memory instrumentation, timeout enforcement, and YAML schema changes to reflection_scheduler.py
  - Agent Type: builder
  - Resume: true

- **Builder (log-rotation)**
  - Name: log-rotation-builder
  - Role: Switch FileHandler to RotatingFileHandler in bridge/telegram_bridge.py
  - Agent Type: builder
  - Resume: true

- **Builder (crash-detection)**
  - Name: crash-detection-builder
  - Role: Wire watchdog to call crash_tracker.log_crash() on bridge death detection
  - Agent Type: builder
  - Resume: true

- **Builder (docs-auditor-cap)**
  - Name: docs-auditor-builder
  - Role: Add per-run API call cap to DocsAuditor
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: observability-validator
  - Role: Verify all success criteria, run tests, check log output
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-updater
  - Role: Update feature docs for reflections observability
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add memory instrumentation and timeout to reflection scheduler
- **Task ID**: build-scheduler-guards
- **Depends On**: none
- **Validates**: tests/unit/test_reflection_scheduler.py (update + new tests)
- **Assigned To**: scheduler-builder
- **Agent Type**: builder
- **Parallel**: true
- Add optional `timeout` field to `ReflectionEntry` dataclass (default: 1800 for function, 3600 for agent)
- Update `load_registry()` to parse `timeout` from YAML
- Add `timeout` validation in `ReflectionEntry.validate()` (must be positive if set)
- Wrap `execute_function_reflection()` call in `asyncio.wait_for(coro, timeout=entry.timeout)`
- Add `psutil.Process(os.getpid()).memory_info().rss` snapshots before/after reflection execution in `run_reflection()`
- Log memory delta, warn if > 100MB
- Handle `asyncio.TimeoutError` in `run_reflection()` -- mark as error with timeout message
- Add tests for timeout parsing, timeout enforcement (mock), memory delta logging

### 2. Switch to RotatingFileHandler for bridge.log
- **Task ID**: build-log-rotation
- **Depends On**: none
- **Validates**: manual verification (log rotation is runtime behavior)
- **Assigned To**: log-rotation-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge/telegram_bridge.py` (line 527), replace `logging.FileHandler(LOG_DIR / "bridge.log")` with `logging.handlers.RotatingFileHandler(LOG_DIR / "bridge.log", maxBytes=10*1024*1024, backupCount=5)`
- Add `import logging.handlers` if not present
- Verify existing formatter and filter are still applied to the new handler

### 3. Wire watchdog crash detection to crash tracker
- **Task ID**: build-crash-detection
- **Depends On**: none
- **Validates**: tests/unit/test_bridge_watchdog.py (update)
- **Assigned To**: crash-detection-builder
- **Agent Type**: builder
- **Parallel**: true
- In `bridge_watchdog.py::check_bridge_health()`, when `is_bridge_running()` returns `(False, None)`, call `log_crash("bridge_dead_on_watchdog_check")`
- Ensure the crash event is logged before recovery escalation
- Add test verifying `log_crash` is called when bridge is detected as not running

### 4. Add API call cap to docs_auditor
- **Task ID**: build-docs-auditor-cap
- **Depends On**: none
- **Validates**: tests/unit/test_docs_auditor.py (update)
- **Assigned To**: docs-auditor-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `max_api_calls: int = 50` parameter to `DocsAuditor.__init__()`
- Add counter that increments on each Anthropic API call
- When counter reaches cap, log a WARNING and stop processing remaining files (do not raise -- return partial results)
- Add test for cap enforcement: mock API calls, verify processing stops at cap

### 5. Validate all changes
- **Task ID**: validate-all-guards
- **Depends On**: build-scheduler-guards, build-log-rotation, build-crash-detection, build-docs-auditor-cap
- **Assigned To**: observability-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_reflection_scheduler.py tests/unit/test_bridge_watchdog.py tests/unit/test_docs_auditor.py -v`
- Verify `python scripts/reflections.py --dry-run` completes without errors
- Check that `config/reflections.yaml` is valid and loadable
- Verify all success criteria are met

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all-guards
- **Assigned To**: docs-updater
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` with timeout field documentation, memory instrumentation, log rotation
- Update `docs/features/README.md` index if needed

### 7. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: observability-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify lint: `python -m ruff check .`
- Verify format: `python -m ruff format --check .`
- Confirm all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_reflection_scheduler.py tests/unit/test_bridge_watchdog.py tests/unit/test_docs_auditor.py -v` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Registry loads | `python -c "from agent.reflection_scheduler import load_registry; entries = load_registry(); assert any(hasattr(e, 'timeout') for e in entries if e.name == 'health-check')"` | exit code 0 |
| Dry run works | `python scripts/reflections.py --dry-run` | exit code 0 |
| RotatingFileHandler in use | `grep -c 'RotatingFileHandler' bridge/telegram_bridge.py` | output > 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. **Memory warning threshold**: The issue suggests 100MB. Is this the right threshold, or should it be lower (50MB) given the bridge's 800MB critical threshold? Higher thresholds miss problems; lower thresholds create noise.
2. **Deferred `log-health-check` reflection**: The issue's Layer 3 (automated log scanning that posts GitHub issues for recurring error patterns) is deferred to keep this Medium appetite. Should it be tracked as a follow-up issue now, or wait until this ships?
3. **`docs_auditor` cap of 50 API calls**: With ~30-50 doc files currently in `docs/`, a cap of 50 means it can audit all files. But if the docs directory grows, the cap may need adjustment. Is 50 the right default, or should it be higher (100)?
