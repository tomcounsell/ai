---
status: Complete
type: feature
appetite: Medium
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/495
last_comment_id:
---

# Bridge Resilience: Graceful Degradation for Dependency Outages

## Problem

On 2026-03-23, a cascade of failures exposed gaps in the bridge's resilience. The bridge crash-looped 10 times in 2 minutes due to Telegram session lock conflicts. The watchdog killed active SDK processes. SDK retries had no backoff. Redis failures were uncoordinated. Reflections crashed on missing prerequisites.

**Current behavior:**
- Startup retry only covers SQLite lock errors (3 attempts at 2s/5s/10s). Other Telegram connection failures crash immediately and rely on launchd restart, creating tight crash loops.
- SDK client has max 2 retries on error but no backoff between them and no circuit breaker for sustained outages.
- Redis operations individually wrapped in try/except (graceful per-call) but no connection-level health tracking or coordinated degradation.
- Reflections crash with full tracebacks when prerequisites (config files, GitHub labels) are missing.
- No degraded mode -- when Anthropic API is down, messages are silently lost.

**Desired outcome:**
- Bridge survives temporary outages of any single dependency without crash-looping
- Startup uses exponential backoff with jitter for all connection failure types
- External API calls use circuit breakers that prevent resource waste during outages
- Degraded mode: when Anthropic is down, bridge acknowledges Telegram messages and queues them
- Reflections validate prerequisites before attempting operations

## Prior Art

No directly related closed issues or merged PRs found. Relevant recent commits inform the solution:
- `6ef1117f`: Fix zombie cleanup killing active SDK processes (exit code 143)
- `44996159`: Fix watchdog killing healthy bridge sessions when zombies detected
- `949e9a31`: Fix zombie Claude Code process accumulation
- `44ad4569`: Comprehensive resilience overhaul (activity tracking, circuit breaker, stall detection)
- `c4b70812`: Observer circuit breaker with exponential backoff

The observer circuit breaker pattern (graduated backoff) is the closest prior art and will be generalized into the reusable module.

## Data Flow

Traces the failure propagation path that this plan addresses:

1. **Entry point**: External dependency becomes unavailable (Telegram API, Anthropic API, or Redis)
2. **Bridge startup** (`telegram_bridge.py:1362-1380`): Telethon `client.start()` fails. Currently only SQLite lock is retried; other errors propagate to `main()` and crash the process. Launchd restarts immediately, creating a crash loop.
3. **SDK query** (`sdk_client.py:910-1030`): `client.query()` fails. Error is fed back to agent up to 2 times with no delay. On sustained outage, retries exhaust instantly and the session fails.
4. **Message delivery**: Failed deliveries go to `bridge/dead_letters.py` (Redis-backed). If Redis is also down, the dead letter persist itself fails silently.
5. **Reflections** (`scripts/reflections.py`): Scheduled tasks attempt operations that depend on files/labels/APIs existing. Missing prerequisites cause full tracebacks instead of graceful skips.
6. **Watchdog** (`monitoring/bridge_watchdog.py`): External process checks bridge health every 60s. Currently has no visibility into per-dependency circuit state.

**With this plan, the flow becomes:**
1. Dependency fails -> circuit breaker records failure, increments counter
2. If threshold exceeded -> circuit opens, calls short-circuit immediately
3. Bridge enters degraded mode for that dependency (e.g., queue messages if Anthropic is down)
4. Periodic health probes test recovery
5. Circuit closes -> normal operation resumes, queued work replays

## Architectural Impact

- **New module**: `bridge/resilience.py` -- reusable CircuitBreaker class, extracted from observer pattern
- **New module**: `bridge/health.py` -- dependency health tracking, consumed by watchdog
- **Interface changes**: `sdk_client.query()` wraps calls with circuit breaker (internal change, no API change)
- **Coupling**: Reduces coupling by centralizing retry/backoff logic instead of per-call ad hoc handling
- **Data ownership**: Health state owned by `bridge/health.py`, read-only by watchdog
- **Reversibility**: High -- each component is independently revertable. Circuit breakers can be disabled by setting thresholds to infinity.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on degraded mode UX)
- Review rounds: 1 (code review)

## Prerequisites

No prerequisites -- this work uses only stdlib and existing packages (no new external dependencies).

## Solution

### Key Elements

- **Unified CircuitBreaker class** (`bridge/resilience.py`): Reusable circuit breaker with configurable failure threshold, backoff schedule, and half-open probe logic. States: closed (normal), open (failing, calls short-circuit), half-open (probing recovery).
- **Startup retry enhancement** (`telegram_bridge.py`): Replace SQLite-only retry with general connection retry covering all Telethon errors. Exponential backoff with jitter, capped at ~5 minutes before falling through to launchd restart.
- **SDK circuit breaker** (`sdk_client.py`): Wrap query calls with CircuitBreaker. On sustained Anthropic failures, open circuit and fail fast rather than hammering API.
- **Degraded mode handler** (`bridge/telegram_bridge.py`): When Anthropic circuit is open, acknowledge incoming Telegram messages with a brief status message and queue them via dead-letter mechanism for replay when circuit closes.
- **Reflections pre-flight** (`scripts/reflections.py`): Each reflection task validates prerequisites (file exists, label exists, API reachable) before attempting work. Missing prerequisites produce a single warning log line, not a traceback.
- **Dependency health status** (`bridge/health.py`): Tracks per-dependency circuit states. Exposes summary for watchdog log checks and bridge status reporting.

### Flow

**Telegram message arrives** -> Check Anthropic circuit state -> If closed: process normally -> If open: acknowledge on Telegram ("Processing delayed"), persist to dead-letter queue -> When circuit closes: replay queued messages

**Bridge startup** -> Attempt Telethon connect -> On failure: backoff with jitter (2s, 4s, 8s, 16s, 32s, 64s, 128s, 256s cap) -> After max attempts: exit cleanly for launchd restart at normal interval

### Technical Approach

- Extract observer circuit breaker pattern into parameterized `CircuitBreaker` class with: `failure_threshold` (int), `backoff_schedule` (list of seconds), `half_open_interval` (seconds), `on_open` / `on_close` callbacks
- Circuit breaker instances created per dependency: `telegram_cb`, `anthropic_cb`, `redis_cb`
- Startup retry uses `asyncio.sleep` with jitter: `base * (2 ** attempt) + random(0, base)`
- Degraded mode reuses existing `dead_letters.py` for message persistence -- no new storage mechanism
- Health module aggregates circuit states into a dict that watchdog reads from a shared location (file or in-process if same process)
- Reflections pre-flight is a decorator or context manager pattern: `@preflight_check(requires=["config/projects.json", "github:reflections-label"])`

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `except Exception: pass` blocks in `telegram_bridge.py`, `sdk_client.py`, and `scripts/reflections.py` -- each must log or change state
- [ ] Circuit breaker `on_open` callback must log at WARNING level with dependency name and failure count
- [ ] Degraded mode acknowledgment must be observable in test (message sent to Telegram)

### Empty/Invalid Input Handling
- [ ] CircuitBreaker handles zero-threshold (always open) and infinite-threshold (never opens) edge cases
- [ ] Health status returns valid dict even when no circuits are registered
- [ ] Startup retry handles immediate success (no unnecessary sleep)

### Error State Rendering
- [ ] Degraded mode Telegram acknowledgment includes human-readable status
- [ ] Watchdog health output includes per-dependency state in parseable format

## Test Impact

- [ ] `tests/unit/test_bridge_logic.py` -- UPDATE: add tests for new startup retry logic covering non-SQLite errors
- [ ] `tests/unit/test_bridge_watchdog.py` -- UPDATE: add assertions for health status parsing from new `bridge/health.py`
- [ ] `tests/unit/test_sdk_client.py` -- UPDATE: add tests for circuit breaker wrapping around query calls

No existing tests need DELETE or REPLACE -- changes are additive. The existing startup retry test (if any) will need its assertions broadened to cover all error types, not just SQLite locks.

## Rabbit Holes

- **Distributed circuit breakers via Redis** -- Tempting to share circuit state across processes, but the bridge is a single process. In-memory state is sufficient. Redis-backed circuit state would add complexity and a circular dependency (Redis circuit breaker stored in Redis).
- **HTTP health endpoint** -- The bridge does not serve HTTP. Exposing health via HTTP would require adding an HTTP server, which is out of scope. Use file-based or in-process health reporting instead.
- **Per-request retry with different strategies** -- Each API call could have its own retry config, but this creates a combinatorial explosion. Circuit breaker at the dependency level is the right granularity.
- **Automatic failover to alternative LLM providers** -- Out of scope. The bridge is tightly coupled to Anthropic SDK. Multi-provider support is a separate project.

## Risks

### Risk 1: Degraded mode message queue grows unbounded during long outage
**Impact:** Memory pressure if Anthropic is down for hours and messages keep arriving
**Mitigation:** Dead-letter queue is Redis-backed (not in-memory). Add a TTL on dead letters (24h) and a max queue size. Log warning when queue exceeds threshold.

### Risk 2: Circuit breaker thresholds too aggressive or too lenient
**Impact:** Too aggressive = normal transient errors trigger degraded mode. Too lenient = bridge hammers failing API.
**Mitigation:** Start conservative (5 failures in 60s to open, 30s half-open probe). Make thresholds configurable via environment variables. Log all state transitions for tuning.

### Risk 3: Startup backoff delays recovery after brief outages
**Impact:** Bridge takes minutes to reconnect after a 2-second Telegram blip
**Mitigation:** First retry is immediate (0s), then exponential. Jitter prevents thundering herd. Launchd KeepAlive ensures restart even if all retries exhaust.

## Race Conditions

### Race 1: Circuit state read during transition
**Location:** `bridge/resilience.py` -- CircuitBreaker state check vs state update
**Trigger:** Concurrent coroutines check circuit state while another coroutine is recording a failure and transitioning to open
**Data prerequisite:** Failure count must be consistent across reads
**State prerequisite:** Circuit state (closed/open/half-open) must be atomically updated
**Mitigation:** Use `asyncio.Lock` for state transitions. State reads (is_open check) can be lock-free since they read a single enum value -- Python's GIL ensures atomic reads of simple attributes.

### Race 2: Dead-letter replay vs new message processing
**Location:** `bridge/dead_letters.py` replay + `bridge/telegram_bridge.py` message handler
**Trigger:** Circuit closes, dead letters replay while new messages also arrive
**Data prerequisite:** Dead letters from outage period must be replayed before new messages to preserve ordering
**State prerequisite:** Circuit must be fully closed before replay begins
**Mitigation:** Replay dead letters in the circuit `on_close` callback before resuming normal message processing. Use a replay lock to prevent concurrent replays.

## No-Gos (Out of Scope)

- HTTP health check endpoint (bridge does not serve HTTP)
- Distributed circuit breaker state across multiple processes
- Multi-LLM provider failover
- Watchdog architecture changes (watchdog stays external, reads health status)
- Redis connection pooling or reconnection logic (existing per-operation try/except is adequate)
- Telegram reconnection logic (Telethon handles this internally; we only need startup retry)

## Update System

No update system changes required -- this feature modifies bridge-internal code only. No new dependencies, no new config files, no migration steps. The existing `scripts/remote-update.sh` and launchd configuration remain unchanged. The new modules (`bridge/resilience.py`, `bridge/health.py`) are picked up automatically by git pull.

## Agent Integration

No agent integration required -- this is a bridge-internal change. The resilience module operates below the agent layer (between the bridge and external dependencies). No new MCP servers, no changes to `.mcp.json`, no new tools exposed to the agent. The bridge imports the new modules directly. The agent's behavior is unchanged -- it simply experiences fewer failures because the bridge handles outages gracefully.

## Documentation

- [ ] Create `docs/features/bridge-resilience.md` describing the resilience architecture: circuit breaker pattern, degraded mode, startup retry, health reporting
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `docs/features/bridge-self-healing.md` to reference the new resilience module and health status integration with watchdog
- [ ] Inline docstrings on `CircuitBreaker` class, `bridge/health.py` module, and reflections pre-flight decorator

## Success Criteria

- [ ] Bridge survives a 60-second simulated Telegram API outage without crash-looping
- [ ] Bridge survives a 60-second simulated Anthropic API outage: queues messages, acknowledges on Telegram, replays when API returns
- [ ] Startup retries use exponential backoff with jitter for all connection error types, not just SQLite locks
- [ ] CircuitBreaker class is reusable: parameterized, used by at least 2 dependencies (Anthropic, startup)
- [ ] Reflections tasks log a single warning (not a traceback) when prerequisites are missing
- [ ] Watchdog health check can read per-dependency circuit state
- [ ] No new external dependencies added
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (resilience-core)**
  - Name: resilience-builder
  - Role: Implement CircuitBreaker class, health module, and startup retry enhancement
  - Agent Type: async-specialist
  - Resume: true

- **Builder (integration)**
  - Name: integration-builder
  - Role: Wire circuit breakers into sdk_client and telegram_bridge, implement degraded mode and reflections pre-flight
  - Agent Type: builder
  - Resume: true

- **Validator (resilience)**
  - Name: resilience-validator
  - Role: Verify circuit breaker behavior, degraded mode, and startup retry under simulated failures
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation and update self-healing docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build CircuitBreaker class and health module
- **Task ID**: build-resilience-core
- **Depends On**: none
- **Validates**: tests/unit/test_circuit_breaker.py (create)
- **Assigned To**: resilience-builder
- **Agent Type**: async-specialist
- **Parallel**: true
- Create `bridge/resilience.py` with `CircuitBreaker` class: states (closed/open/half-open), configurable failure threshold, backoff schedule, half-open probe interval, on_open/on_close callbacks, asyncio.Lock for state transitions
- Create `bridge/health.py` with `DependencyHealth` class: register circuit breakers, expose summary dict, provide formatted status for watchdog
- Write unit tests in `tests/unit/test_circuit_breaker.py`: state transitions, threshold behavior, half-open recovery, concurrent access, edge cases (zero threshold, immediate success)

### 2. Enhance startup retry
- **Task ID**: build-startup-retry
- **Depends On**: build-resilience-core
- **Validates**: tests/unit/test_bridge_logic.py (update)
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace SQLite-only retry in `telegram_bridge.py:1362-1380` with general connection retry: catch all `Exception` from `client.start()`, use exponential backoff with jitter (0s, 2s, 4s, 8s, 16s, 32s, 64s, 128s, 256s cap), max 8 attempts before clean exit
- Update `tests/unit/test_bridge_logic.py` with tests for non-SQLite connection failures

### 3. Wire SDK circuit breaker and degraded mode
- **Task ID**: build-sdk-circuit
- **Depends On**: build-resilience-core
- **Validates**: tests/unit/test_sdk_client.py (update)
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Create Anthropic circuit breaker instance in `sdk_client.py` with threshold=5, backoff=[30, 60, 120, 240, 480]
- Wrap `query()` method: check circuit before calling SDK, record success/failure after
- In `telegram_bridge.py` message handler: when Anthropic circuit is open, send acknowledgment to Telegram and persist message to dead-letter queue
- Add `on_close` callback to replay dead letters when Anthropic circuit recovers
- Update `tests/unit/test_sdk_client.py` with circuit breaker integration tests

### 4. Add reflections pre-flight checks
- **Task ID**: build-preflight
- **Depends On**: none
- **Validates**: tests/unit/test_reflections_preflight.py (create)
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: true
- Create pre-flight validation utility (decorator or function) that checks file existence, GitHub label existence, API reachability
- Apply to reflections tasks in `scripts/reflections.py`: wrap each task with pre-flight check, catch failures as single-line warnings
- Write tests in `tests/unit/test_reflections_preflight.py`

### 5. Integrate health status with watchdog
- **Task ID**: build-health-watchdog
- **Depends On**: build-resilience-core, build-sdk-circuit
- **Validates**: tests/unit/test_bridge_watchdog.py (update)
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `monitoring/bridge_watchdog.py` to read health status from `bridge/health.py`
- Include per-dependency circuit state in watchdog health check output
- Update `tests/unit/test_bridge_watchdog.py` with health status assertions

### 6. Validate all components
- **Task ID**: validate-resilience
- **Depends On**: build-startup-retry, build-sdk-circuit, build-preflight, build-health-watchdog
- **Assigned To**: resilience-validator
- **Agent Type**: test-engineer
- **Parallel**: false
- Run full test suite: `pytest tests/unit/ -x -q`
- Verify circuit breaker state transitions under simulated failures
- Verify degraded mode acknowledgment message format
- Verify startup retry covers non-SQLite errors
- Verify reflections pre-flight logs warnings (not tracebacks) for missing prerequisites

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-resilience
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/bridge-resilience.md`
- Add entry to `docs/features/README.md` index table
- Update `docs/features/bridge-self-healing.md` with resilience module references
- Add inline docstrings to new modules

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: resilience-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full validation: `pytest tests/ -x -q`
- Run lint: `python -m ruff check .`
- Run format check: `python -m ruff format --check .`
- Verify all success criteria met including documentation

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Resilience module exists | `python -c "from bridge.resilience import CircuitBreaker"` | exit code 0 |
| Health module exists | `python -c "from bridge.health import DependencyHealth"` | exit code 0 |
| Circuit breaker unit tests | `pytest tests/unit/test_circuit_breaker.py -v` | exit code 0 |
| No new dependencies | `git diff HEAD -- requirements.txt setup.py pyproject.toml` | output contains  |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

1. **Degraded mode message format** -- What exact text should the bridge send when Anthropic is down? Proposed: "I've received your message but my AI backend is temporarily unavailable. I'll process it automatically when service recovers." Is this appropriate, or should it be shorter/different?

2. **Circuit breaker thresholds** -- Proposed defaults: 5 failures in 60s to open circuit, 30s half-open probe interval. Are these reasonable starting points, or should they be more/less aggressive?
