---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/482
last_comment_id:
---

# Migrate Raw Redis Anti-Patterns to Popoto Models

## Problem

Four files create their own `redis.Redis()` or `redis.from_url()` clients and use raw key/value operations instead of Popoto models. This bypasses the ORM's schema validation, connection pooling, TTL management, and query API.

**Current behavior:**
- `monitoring/health.py` creates `redis.Redis(host="localhost", port=6379, ...)` just to ping and read memory info
- `bridge/dedup.py` creates `redis.from_url()` with a module-level `_redis_client` cache, uses manual `expire()` calls
- ~~`scripts/issue_poller.py`~~ — removed in #565, no longer needs migration
- `monitoring/telemetry.py` (exists in worktrees, pending merge) creates `redis.Redis()` for observer telemetry counters and event lists

**Desired outcome:**
- All four files use Popoto models or `POPOTO_REDIS_DB` for raw atomics
- Zero standalone `redis.Redis()` or `redis.from_url()` instantiations outside test fixtures
- Data is queryable, type-safe, and uses Popoto's TTL mechanism where applicable

## Prior Art

- **Issue #161 / PR #166**: Redis Migration wave 1 -- consolidated initial persistence into Popoto. Succeeded and established the migration pattern used here.
- **Issue #163**: Refactor daydream to leverage unified Redis persistence -- related consolidation effort, completed.
- **PR #392**: Strengthen Popoto model relationships and naming -- model cleanup pass that set naming conventions.
- **Issue #437**: Create OOP/data modeling audit skill -- broader initiative; this work addresses a subset of its findings.

## Data Flow

1. **`monitoring/health.py`**: `HealthChecker.check_database()` -> creates `redis.Redis()` -> calls `ping()` + `info("memory")` -> returns `HealthCheckResult`. No persistent data stored. Connection is used transiently.

2. **`bridge/dedup.py`**: Telegram message arrives -> `is_duplicate_message(chat_id, msg_id)` checks Redis set -> if not duplicate, `record_message_processed()` adds to set, trims to 50, sets 2h TTL. Data flow: `bridge/telegram_bridge.py` -> `dedup.py` -> Redis set per chat.

3. ~~**`scripts/issue_poller.py`**~~ — removed in #565, no longer needs migration.

4. **`monitoring/telemetry.py`** (worktree): Observer agent calls `record_decision()` / `record_interjection()` -> increments hash counters and appends to capped list -> `get_health()` reads counters for dashboard. All data has daily rollup keys with 7-day TTL.

## Architectural Impact

- **New dependencies**: None -- Popoto is already a core dependency
- **Interface changes**: Internal function signatures remain the same; callers are unaffected
- **Coupling**: Decreases coupling -- removes 4 independent Redis connection management patterns in favor of the shared `POPOTO_REDIS_DB` connection pool
- **Data ownership**: Moves data ownership from ad-hoc Redis keys to typed Popoto models in `models/`
- **Reversibility**: Easy -- models can be reverted to raw Redis calls without data loss since the underlying Redis keys are the same

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Solo dev work. Each file is an independent migration with no cross-file dependencies. The pattern is well-established from PR #166.

## Prerequisites

No prerequisites -- this work has no external dependencies. Popoto is already installed and configured.

## Solution

### Key Elements

- **`DedupRecord` model** (`models/dedup.py`): Popoto model with `KeyField` for chat_id, storing message ID sets with `Meta.ttl = 7200`
- **`SeenIssue` model** (`models/seen_issue.py`): Popoto model with `KeyField` for org/repo, storing seen issue number sets (persistent, no TTL)
- **`ObserverTelemetry` model** (`models/telemetry.py`): Popoto model with counter fields and list field for recent interjections, with daily rollup keys using 7-day TTL
- **`POPOTO_REDIS_DB` swap in health.py**: Replace `redis.Redis()` instantiation with `from popoto.redis_db import POPOTO_REDIS_DB`

### Flow

No user-facing flow changes. All changes are internal plumbing -- callers of each module continue to use the same public API.

### Technical Approach

- **Tier 1 (direct model migration)**: `bridge/dedup.py` and `monitoring/telemetry.py` get full Popoto models replacing all raw Redis operations
- ~~**Tier 2 (partial migration)**~~: `scripts/issue_poller.py` — removed in #565, no longer needs migration
- **Tier 2 (connection swap only)**: `monitoring/health.py` replaces `redis.Redis(host="localhost", ...)` with `POPOTO_REDIS_DB` -- no model needed since it only reads transient server state
- Each migration is independent and can be built/tested in isolation
- Existing public function signatures are preserved to avoid breaking callers

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `bridge/dedup.py` has `except Exception` blocks in both `is_duplicate_message` and `record_message_processed` -- tests must verify these log warnings and return safe defaults (False / None)
- [ ] `monitoring/health.py` has `except Exception` in `check_database` -- test must verify it returns UNHEALTHY status on connection failure
- ~~`scripts/issue_poller.py`~~ — removed in #565, no longer applicable
- [ ] `monitoring/telemetry.py` wraps all writes in try/except -- tests must verify telemetry failures never propagate to callers

### Empty/Invalid Input Handling
- [ ] `bridge/dedup.py`: test with chat_id=None, message_id=0, negative message_id
- ~~`scripts/issue_poller.py`~~ — removed in #565, no longer applicable

### Error State Rendering
- [ ] No user-visible output in any of these modules -- error state rendering is not applicable

## Test Impact

- [ ] `tests/unit/test_telemetry.py` (cached .pyc only, no source on main) -- if telemetry.py lands from another branch first, tests will need UPDATE to use the new model API
- [ ] `tests/unit/test_duplicate_delivery.py` -- UPDATE: may reference dedup internals; verify imports still work after migration
- [ ] `tests/unit/test_session_watchdog.py` -- UPDATE: references health check; verify HealthChecker still works with POPOTO_REDIS_DB

No existing tests affected for `bridge/dedup.py` -- it currently has zero test coverage (new tests will be created).

## Rabbit Holes

- **Migrating the distributed lock to Popoto**: Popoto's `save()` cannot do atomic `SET NX EX`. The lock must stay as raw Redis. Do not attempt to wrap it in a model.
- **Migrating `agent/steering.py`**: This is an intentional transient FIFO queue design (documented in `docs/features/steering-queue.md`). Explicitly out of scope.
- **Migrating `agent/job_queue.py`**: Contains ORM-repair code that intentionally uses raw Redis. Out of scope.
- **Key migration/data continuity**: Do not build migration scripts for existing Redis keys. The data is ephemeral (TTLs of hours to days) and will naturally rotate.

## Risks

### Risk 1: Telemetry.py not yet on main
**Impact:** If `monitoring/telemetry.py` has not landed on main when this work starts, the telemetry migration task has no source file to modify.
**Mitigation:** The builder should check if `monitoring/telemetry.py` exists on the build branch. If not, create the model in `models/telemetry.py` and create `monitoring/telemetry.py` using the model from scratch, following the patterns in the worktree version.

### Risk 2: Dedup model TTL behavior differs from manual expire
**Impact:** Popoto `Meta.ttl` sets TTL on the model's root key, but the current code sets TTL on individual per-chat set keys. If the model uses a single key, all chats share one TTL.
**Mitigation:** Use `KeyField` for chat_id so each chat gets its own Redis key with independent TTL via Popoto's per-instance `_ttl` or `Meta.ttl`.

## Race Conditions

### Race 1: Concurrent dedup checks during bridge catch_up
**Location:** `bridge/dedup.py` -- `is_duplicate_message` + `record_message_processed`
**Trigger:** Multiple catch_up replays running concurrently could check and record the same message ID
**Data prerequisite:** The dedup set for the chat must exist before the check
**State prerequisite:** Redis must be available
**Mitigation:** The current behavior (set-based dedup) is inherently idempotent -- `sadd` is safe under concurrent access. Popoto model saves are also idempotent for this use case. No additional locking needed.

### ~~Race 2: Issue poller distributed lock~~
Removed in #565 -- issue poller no longer exists.

## No-Gos (Out of Scope)

- Migrating `agent/steering.py` (intentional transient FIFO design)
- Migrating `agent/job_queue.py` ORM-repair code (intentionally raw)
- Building data migration scripts for existing Redis keys (data is ephemeral)
- Changing any public API signatures of the migrated modules

## Update System

No update system changes required -- this is purely internal refactoring. No new dependencies, config files, or migration steps. The existing Popoto dependency is already deployed on all machines.

## Agent Integration

No agent integration required -- this is internal plumbing. No new MCP servers, no `.mcp.json` changes, no bridge import changes. The migrated modules are consumed internally by the bridge and monitoring systems, not exposed as agent tools.

## Documentation

- ~~Update `docs/features/issue-poller.md`~~ — removed in #565, no longer applicable
- [ ] Add inline docstrings to new model classes in `models/dedup.py` and `models/telemetry.py`
- [ ] Add entries to `models/__init__.py` for new models

## Success Criteria

- [ ] `monitoring/health.py` uses `POPOTO_REDIS_DB` instead of `redis.Redis()` -- no standalone client instantiation
- [ ] `bridge/dedup.py` uses a Popoto model with `Meta.ttl` -- no `redis.from_url()` or manual `expire()` calls
- ~~`scripts/issue_poller.py`~~ — removed in #565, no longer applicable
- [ ] `monitoring/telemetry.py` uses a Popoto model -- no `redis.Redis()` instantiation
- [ ] Zero standalone `redis.Redis()` or `redis.from_url()` outside test fixtures and `agent/job_queue.py`
- [ ] New tests for `bridge/dedup.py` covering duplicate detection, recording, TTL, and error handling
- [ ] Existing tests updated and passing
- [ ] `ruff check` and `ruff format` pass

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: model-builder
  - Role: Create Popoto models and migrate all 4 files
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: test-writer
  - Role: Write new tests for dedup, update existing tests
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: migration-validator
  - Role: Verify zero raw Redis instantiations remain, all tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create Popoto Models
- **Task ID**: build-models
- **Depends On**: none
- **Validates**: `pytest tests/unit/ -x -q`
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `models/dedup.py` with `DedupRecord` model (KeyField for chat_id, set-like storage, Meta.ttl = 7200)
- ~~Create `models/seen_issue.py` with `SeenIssue` model~~ — removed in #565, no longer applicable
- Create `models/telemetry.py` with `ObserverTelemetry` model (counter fields, list field, daily rollup with 7-day TTL)
- Register all new models in `models/__init__.py`

### 2. Migrate Health Check (Tier 3)
- **Task ID**: build-health
- **Depends On**: none
- **Validates**: `pytest tests/unit/test_session_watchdog.py -x -q`
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `redis.Redis(host="localhost", port=6379, socket_timeout=2)` with `from popoto.redis_db import POPOTO_REDIS_DB`
- Update `check_database()` to use `POPOTO_REDIS_DB.ping()` and `POPOTO_REDIS_DB.info("memory")`
- Remove `import redis` from health.py

### 3. Migrate Dedup (Tier 1)
- **Task ID**: build-dedup
- **Depends On**: build-models
- **Validates**: `pytest tests/unit/test_dedup.py -x -q` (create)
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `bridge/dedup.py` to use `DedupRecord` model instead of raw Redis sets
- Remove `_redis_client`, `_get_redis()`, and `redis.from_url()` usage
- Preserve existing public API: `is_duplicate_message()`, `record_message_processed()`

### ~~4. Migrate Issue Poller Seen-Tracking (Tier 2)~~
Removed in #565 -- issue poller no longer exists. Skip this task.

### 5. Migrate Telemetry (Tier 1)
- **Task ID**: build-telemetry
- **Depends On**: build-models
- **Validates**: `pytest tests/unit/ -x -q`
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: false
- If `monitoring/telemetry.py` exists on the build branch, rewrite it to use `ObserverTelemetry` model
- If it does not exist, create it from scratch using the model, following the worktree version's public API
- Remove `_redis_client`, `_get_redis()`, and `redis.Redis()` usage

### 6. Write Tests
- **Task ID**: build-tests
- **Depends On**: build-dedup, build-telemetry, build-health
- **Validates**: `pytest tests/unit/test_dedup.py -x -q`
- **Assigned To**: test-writer
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `tests/unit/test_dedup.py` with tests for duplicate detection, recording, TTL behavior, and error handling
- Update any existing tests that reference the migrated modules
- Verify all tests pass with `pytest tests/unit/ -x -q`

### 7. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: model-builder
- **Agent Type**: documentarian
- **Parallel**: false
- ~~Update `docs/features/issue-poller.md`~~ — removed in #565, no longer applicable
- Ensure all new model files have complete docstrings

### 8. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: migration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify zero `redis.Redis()` or `redis.from_url()` outside test fixtures and `agent/job_queue.py`
- Run full test suite: `pytest tests/unit/ -x -q`
- Run `python -m ruff check . && python -m ruff format --check .`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No raw Redis clients | `grep -rn 'redis\.Redis(\|redis\.from_url(' --include='*.py' bridge/ monitoring/` | exit code 1 |
| Models registered | `python -c "from models.dedup import DedupRecord"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue is thoroughly scoped with clear tiers, acceptance criteria, and explicit out-of-scope items. The telemetry.py file status (worktree vs main) is handled by the risk mitigation in Risk 1.
