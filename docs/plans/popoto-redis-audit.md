---
status: Shipped
type: chore
appetite: Medium
owner: Valor
created: 2026-02-13
tracking: https://github.com/yudame/valor-agent/issues/90
---

# Audit and update all popoto/redis usage for v1.0.0b2

## Problem

popoto v1.0.0b2 is released with breaking changes (`sort_by` renamed to `partition_by`) and new features (`delete_all()`, `to_dict()`, `auto_now`, `get_or_create()`). Every line of popoto and Redis code in the repo needs review to:

1. Complete the breaking change migration (`sort_by` -> `partition_by`)
2. Adopt useful new features where they simplify existing code
3. Fix the test fixture to handle popoto's new separate async Redis connection
4. Ensure direct Redis usage (dedup, steering) is consistent with popoto's connection management

**Current state:** 16 files touch popoto or Redis across 4 models, 2 agent modules, 3 bridge modules, and 7 test files.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0 (ship it)

## Prerequisites

None.

## Solution

### Inventory of all popoto/redis usage

| File | Type | What it does |
|------|------|-------------|
| `models/sessions.py` | Model | `AgentSession` - session lifecycle tracking |
| `models/telegram.py` | Model | `TelegramMessage` - Telegram message mirror |
| `models/bridge_event.py` | Model | `BridgeEvent` - structured bridge events |
| `models/dead_letter.py` | Model | `DeadLetter` - failed delivery persistence |
| `agent/job_queue.py` | Model + logic | `RedisJob` model + queue worker (sync + async) |
| `agent/steering.py` | Direct Redis | Steering queue via `POPOTO_REDIS_DB` Lists |
| `bridge/dedup.py` | Direct Redis | Message dedup via raw `redis` Sets |
| `bridge/dead_letters.py` | Popoto async | Dead letter replay via `DeadLetter` model |
| `bridge/telegram_bridge.py` | Integration | Orchestrates modules that use popoto |
| `tests/conftest.py` | Test infra | `redis_test_db` fixture (db isolation) |
| `tests/test_redis_models.py` | Tests | 36 tests across all 4 models |
| `tests/test_steering.py` | Tests | Steering queue tests |
| `tests/test_auto_continue.py` | Tests | Auto-continue with steering |
| `tests/test_remote_update.py` | Tests | Mocks RedisJob |
| `pyproject.toml` | Config | Dependency pin |
| `.env.example` | Config | Redis URL (default localhost) |

### Work breakdown

#### Task 1: Core migration (breaking change)

Update `pyproject.toml` dependency and rename all `sort_by` to `partition_by`:

- `pyproject.toml` line 15: `popoto>=0.9.0` -> `popoto>=1.0.0b2`
- `models/sessions.py` line 20: `sort_by="project_key"` -> `partition_by="project_key"`
- `models/telegram.py` line 21: `sort_by="chat_id"` -> `partition_by="chat_id"`
- `agent/job_queue.py` line 45: `sort_by="project_key"` -> `partition_by="project_key"`
- Run `uv sync`

#### Task 2: Fix test fixture for async Redis isolation

popoto v1.0.0b2 maintains a separate `_POPOTO_ASYNC_REDIS_DB` connection. The `redis_test_db` conftest fixture only switches the sync connection to db=1, leaving async operations hitting db=0.

- `tests/conftest.py`: Reset `rdb._POPOTO_ASYNC_REDIS_DB = None` and call `set_async_redis_db_settings(db=1)` in the fixture setup/teardown

#### Task 3: Review models for new feature adoption

Evaluate each model for `auto_now` / `auto_now_add` on timestamp fields:

- `models/sessions.py`: `started_at` could use `auto_now_add=True`, `last_activity` could use `auto_now=True`
- `models/telegram.py`: `timestamp` - check if caller always passes `time.time()`
- `models/bridge_event.py`: `timestamp` - `BridgeEvent.log()` always sets `time.time()`, candidate for `auto_now_add`
- `models/dead_letter.py`: `created_at` - always set to `time.time()`, candidate for `auto_now_add`
- `agent/job_queue.py`: `created_at` - always set to `time.time()` in `_push_job()`, candidate for `auto_now_add`

Evaluate `to_dict()` for serialization:
- `models/bridge_event.py` line 44: `cleanup_old()` iterates and deletes - could use `delete_all()` with filter
- Any place models are serialized to JSON/dict manually

Evaluate `delete_all()`:
- `models/bridge_event.py`: `cleanup_old()` loops through `query.all()` and deletes individually
- Test cleanup patterns

Evaluate `get_or_create()`:
- `agent/job_queue.py`: Not applicable (always creates new jobs)
- `models/sessions.py`: Could simplify session creation if duplicate checking exists

#### Task 4: Review direct Redis usage

**`agent/steering.py`** - Uses `POPOTO_REDIS_DB` directly for List operations:
- Verify still works with v1.0.0b2's `POPOTO_REDIS_DB`
- Consider: should this use a popoto Model instead? (Probably not - Lists are a better fit)

**`bridge/dedup.py`** - Uses raw `redis` library (not popoto):
- Uses its own `redis.from_url()` connection (line 22-26)
- Not using popoto's connection at all
- Consider: consolidate to use `POPOTO_REDIS_DB` for connection consistency? Or keep separate for isolation.

#### Task 5: Validate and run full test suite

- `grep -r "sort_by=" models/ agent/` should return empty
- `uv pip show popoto` should show 1.0.0b2
- `uv run python -m pytest tests/` full suite passes
- Manual bridge startup test

## Rabbit Holes

- **Don't convert dedup.py to use popoto Models** - Redis Sets are the right tool for dedup; popoto Models would add unnecessary overhead
- **Don't convert steering.py to use popoto Models** - Redis Lists with RPUSH/LPOP are ideal for FIFO queues
- **Don't add new models** - This is an audit, not a feature
- **Don't refactor the job queue architecture** - Just update the dependency layer

## Risks

### Risk 1: `auto_now` behavior differs from manual `time.time()`
**Impact:** Timestamps might be set at different points in the flow
**Mitigation:** Only adopt `auto_now_add` where the timestamp is always set at creation time, never updated later

### Risk 2: `delete_all()` has different semantics than individual deletes
**Impact:** Could miss cleanup of related keys
**Mitigation:** Test `delete_all()` behavior in isolation before adopting

### Risk 3: Async Redis connection isolation between tests
**Impact:** Test flakiness or cross-test pollution
**Mitigation:** Verified fix in conftest.py - reset `_POPOTO_ASYNC_REDIS_DB` and call `set_async_redis_db_settings(db=1)`

## No-Gos (Out of Scope)

- Adding new popoto models
- Refactoring queue architecture
- Changing Redis connection topology
- Adding Redis Cluster support

## Update System

No update system changes required. The update script already handles `uv sync` for dependency updates.

## Agent Integration

No agent integration required. This is an internal dependency upgrade with no new tools or bridge API changes.

## Documentation

- [ ] Update `docs/features/popoto-redis-expansion.md` if new popoto features are adopted
- [ ] Add migration notes to plan doc on completion

## Success Criteria

- [ ] `pyproject.toml` updated to `popoto>=1.0.0b2`
- [ ] All `sort_by` parameters renamed to `partition_by`
- [ ] `tests/conftest.py` handles async Redis db isolation
- [ ] Each model reviewed for `auto_now_add` / `auto_now` adoption
- [ ] `BridgeEvent.cleanup_old()` reviewed for `delete_all()` adoption
- [ ] Direct Redis usage in `dedup.py` and `steering.py` reviewed
- [ ] `uv sync` completes successfully
- [ ] Full test suite passes
- [ ] Bridge starts and processes a message successfully
